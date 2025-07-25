from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone
from io import BytesIO
from typing import List
import requests, os, traceback
import uuid
import json
import shutil
import re
import pyodbc

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_community.chat_models import ChatOllama

from app import build_chain, extract_text_image_link_pairs, DOCUMENTS_FOLDER, ingest

import jwt
from jwt import PyJWKClient

# === INIT ===
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    #allow_origins=["https://g2g-chatbot-geakehf4aqamfcfb.eastasia-01.azurewebsites.net"],
    allow_origins=["http://localhost:5173","http://localhost:3000", "https://g2g-chatbot-geakehf4aqamfcfb.eastasia-01.azurewebsites.net"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TENANT_ID = "18bea863-348d-41f2-b82b-6162e1822bbb"
AUDIENCE = "18bea863-348d-41f2-b82b-6162e1822bbb"
#AUDIENCE = f"api://18bea863-348d-41f2-b82b-6162e1822bbb/user_impersonation"
JWKS_URL = f"https://login.microsoftonline.com/6eb54db1-fc6e-4b0a-a00b-930182dca624/discovery/v2.0/keys"
ISSUER = f"https://login.microsoftonline.com/6eb54db1-fc6e-4b0a-a00b-930182dca624/v2.0"

UPLOAD_DIR = "./uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

chain, retriever, llm = build_chain()

# === MODELS ===
class QueryRequest(BaseModel):
    question: str
    username: str

class FeedbackRequest(BaseModel):
    question: str
    response: str
    feedback: str
    session_id: str = str(uuid.uuid4())
    timestamp: str = datetime.now(timezone.utc).isoformat()

class SignInRequest(BaseModel):
    email: str
    username: str

class MessageSaveRequest(BaseModel):
    session_id: str
    email: str
    sender: str
    text: str
    created_at: str
    image_ids: List[str] = []
    related_links: List[str] = []

class SessionFetchRequest(BaseModel):
    session_id: str
    email: str

# === DB CONNECTION ===
def get_connection():
    #print(pyodbc.drivers())
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        "SERVER=clean-prod.database.windows.net;"
        "DATABASE=G2G_DB;"
        "UID=clean-db;"
        "PWD=Innovation@123"
    )

# === Checking app ===
@app.get("/")
@app.get("/test")
def test():
    return "Welcome to Guide 2 Govern Application"

def validate_token(token: str):
    try:
        jwks_client = PyJWKClient(JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER
        )
        return payload
    except Exception as e:
        print("Token validation error:", e)  # Add this line for debugging
        raise HTTPException(status_code=401, detail="Invalid token")



@app.post("/ask")
async def ask_ollama(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")

    response = requests.post("http://localhost:11434/api/generate", json={
        "model": "llama3.2",
        "prompt": prompt,
        "stream": false
    })
    print("Ollama response status:", response.status_code)
    print("Ollama response text:", response.text)
    response.raise_for_status()
    try:
        return response.json()
    except json.JSONDecodeError:
        print("Ollama returned invalid JSON:", response.text)
        raise HTTPException(status_code=502, detail="Ollama returned invalid response")


@app.post("/api/login")
def login(authorization: str = Header(...)):
    token = authorization.split("Bearer ")[-1]
    user = validate_token(token)
    #print("User authenticated:", user)
    return {
        "status": "success",
        "message": "User authenticated successfully",
        "user": {
            "email": user.get("preferred_username"),
            "name": user.get("name")
        }
    }


# === CHAT ===
GREETINGS = re.compile(r"\b(hi|hello|hey|good morning|good afternoon|good evening|greetings)\b", re.I)

@app.post("/chat")
def chat(req: QueryRequest):
    
    try:
        user_input = req.question.strip()
        username = req.username
        print("Input schema:", chain.input_schema.schema())
        print("User input:", user_input)
        # If greeting or empty, return process flows only
        if not user_input or GREETINGS.search(user_input):
            # Fetch process flows from DB or API
            process_flows = getChatProcessFlow()
            doc_list_str = f"""Hi {username}, \n\n Welcome to G2G Chat bot Agent!! \n\n I'd be happy to help you with process flows.\n\nTo get started, I can offer assistance with the following processes:
                {'\n'.join(f" {f}" for f in process_flows["chat_process_flows"]) if process_flows.get("chat_process_flows") else "(No process flows available)"}"""
            return {
                "answer": doc_list_str,
                "image_ids": [],
                "related_links": []
            }
        else:
            # Cached document retrieval
            relevant_docs_sources = set()
            unique_docs = []
            relevant_docs = retriever.invoke(user_input)
            #print(relevant_docs)  # Print retrieved documents for debugging
            for doc in relevant_docs:
                #print(doc.page_content)
                source = doc.metadata.get("source", "")
                # Defensive: avoid adding bool or non-str types to set
                if isinstance(source, str) and source and (not isinstance(source, bool)) and source not in relevant_docs_sources:
                    relevant_docs_sources.add(source)
                    unique_docs.append(doc)

            print("Relevant document sources:", relevant_docs_sources)
            input_data = {
                "input_documents": unique_docs,  # result of similarity_search or retriever
                "input": user_input
            }

            result = chain.invoke(input_data)

            # Image/link processing (same as before)
            #relevant_docs = docs
            image_ids = []
            related_links = set()
            seen_ids = set()

            for doc in unique_docs:
                fname = doc.metadata.get("source")
                para_idx = doc.metadata.get("para_index")
                if fname is None or para_idx is None:
                    continue
                try:
                    para_idx = int(para_idx)
                except Exception:
                    continue
                path = os.path.join(DOCUMENTS_FOLDER, fname)
                triplets = extract_text_image_link_pairs(path)
                for offset in range(-3, 4):
                    nearby_idx = para_idx + offset
                    if 0 <= nearby_idx < len(triplets):
                        _, imgs, links = triplets[nearby_idx]
                        for i, img in enumerate(imgs):
                            img_id = f"{fname}::img{nearby_idx}_{i}"
                            if img_id not in seen_ids:
                                image_ids.append(img_id)
                                seen_ids.add(img_id)
                        related_links.update(links)

            # ✅ Now return all together
            return {
                "answer":  result["answer"],
                "image_ids": image_ids,
                "related_links": list(related_links)
            }

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
    

@app.post("/signin")
def signin(user: SignInRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # if user exists
        cursor.execute("SELECT ID FROM USER_SETTINGS_TABLE WHERE EMAIL_ID = ?", (user.email,))
        existing_user = cursor.fetchone()

        if not existing_user:
            # Insert user
            cursor.execute("""
                INSERT INTO USER_SETTINGS_TABLE (USER_NAME, EMAIL_ID, CREATED_BY)
                VALUES (?, ?, ?)
            """, (user.username, user.email, user.username))

        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor: cursor.close()
        if conn: conn.close()



@app.post("/save_message")
def save_message(msg: MessageSaveRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 🔍 Get user ID using email
        cursor.execute("SELECT ID FROM USER_SETTINGS_TABLE WHERE EMAIL_ID = ?", (msg.email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
        user_id = result[0]

        # 🕒 Convert timestamp
        created_at = datetime.fromisoformat(msg.created_at.replace("Z", ""))
        prompt_with_sender = f"{msg.sender}|{msg.text}"
        # 📝 Insert into USER_MESSAGES_TABLE
        cursor.execute("""
            INSERT INTO USER_MESSAGES_TABLE
            (USER_ID, SESSION_ID, PROMPTS, MESSAGE, LINKS, CREATE_DATE, CREATED_BY)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            msg.session_id,
            prompt_with_sender,
            msg.text,
            json.dumps(msg.related_links),
            created_at,
            user_id
        ))

        # ✅ Get the auto-generated ID of the inserted message
        message_id = cursor.execute("SELECT @@IDENTITY").fetchval()

        # 🖼️ Save image_ids if any
        for image_id in msg.image_ids:
            cursor.execute("""
                INSERT INTO USER_MESSAGES_IMAGE_TABLE
                (MESSAGE_ID, IMAGE_ID, CREATE_DATE, CREATED_BY)
                VALUES (?, ?, ?, ?)
            """, (
                message_id,
                image_id,
                created_at,
                user_id
            ))

        conn.commit()
        print("✅ Message and associated images saved")
        return {"status": "saved"}

    except Exception as e:
        print("❌ Error in /save_message:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor: cursor.close()
        if conn: conn.close()




@app.post("/get_session")
def get_session(req: SessionFetchRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 🔍 Get user ID from email
        cursor.execute("SELECT ID FROM USER_SETTINGS_TABLE WHERE EMAIL_ID = ?", (req.email,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        user_id = row[0]

        # 🧾 Fetch messages along with MESSAGE_ID
        cursor.execute("""
            SELECT ID, CREATED_BY, PROMPTS, MESSAGE, LINKS, CREATE_DATE
            FROM USER_MESSAGES_TABLE
            WHERE SESSION_ID = ? AND USER_ID = ?
            ORDER BY CREATE_DATE ASC
        """, (req.session_id, user_id))

        results = cursor.fetchall()
        messages = []

        # ...inside get_session...
        for row in results:
            message_id = row[0]
            prompt_with_sender = row[2]  # PROMPTS
            text = row[3]                # MESSAGE
            links = row[4]
            created_at = row[5]

            # Extract sender from PROMPTS
            if "|" in prompt_with_sender:
                sender_type, _ = prompt_with_sender.split("|", 1)
            else:
                sender_type = "user"  # fallback

            message_data = {
                "sender": sender_type,
                "text": text,
                "loading": False,
                "created_at": created_at.isoformat() if created_at else None
            }

            if sender_type == "bot":
                message_data["related_links"] = json.loads(links) if links else []
                cursor.execute("""
                    SELECT IMAGE_ID FROM USER_MESSAGES_IMAGE_TABLE
                    WHERE MESSAGE_ID = ?
                """, (message_id,))
                image_rows = cursor.fetchall()
                image_ids = [img_row[0] for img_row in image_rows]
                message_data["image_ids"] = image_ids

            messages.append(message_data)
        return {"messages": messages, "session_id": req.session_id}

    except Exception as e:
        print("❌ Exception in /get_session:", e)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if cursor: cursor.close()
        if conn: conn.close()





@app.get("/get_history")
def get_history(email: str = Query(...)):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        #  Get USER_ID using email
        cursor.execute("SELECT ID FROM USER_SETTINGS_TABLE WHERE EMAIL_ID = ?", (email,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
        user_id = result[0]

        # Get session IDs and earliest message times for that user
        cursor.execute("""
            SELECT SESSION_ID, MIN(CREATE_DATE)
            FROM USER_MESSAGES_TABLE
            WHERE USER_ID = ?
            GROUP BY SESSION_ID
            ORDER BY MIN(CREATE_DATE) DESC
        """, (user_id,))
        sessions = cursor.fetchall()
        history = []
        for s in sessions:
            #  Get first message in session
            cursor.execute("""
                SELECT MESSAGE FROM USER_MESSAGES_TABLE
                WHERE USER_ID = ? AND SESSION_ID = ?
                ORDER BY CREATE_DATE ASC
            """, (user_id, s[0]))
            first_msg = cursor.fetchone()
            if first_msg:
                history.append({
                    "session_id": s[0],
                    "first_message": first_msg[0]
                })
        return {"history": history[:8]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# === IMAGE ENDPOINT ===
@app.get("/image")
def get_image(image_id: str = Query(...)):
    try:
        fname, img_info = image_id.split("::img")
        para_idx, img_idx = map(int, img_info.split("_"))
        for folder in [DOCUMENTS_FOLDER, UPLOAD_DIR]:
            path = os.path.join(folder, fname)
            if not os.path.exists(path):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext == ".docx":
                triplets = extract_text_image_link_pairs(path)
                if para_idx < len(triplets):
                    _, imgs, _ = triplets[para_idx]
                    if img_idx < len(imgs):
                        buf = BytesIO()
                        imgs[img_idx].save(buf, format="PNG")
                        buf.seek(0)
                        return StreamingResponse(buf, media_type="image/png")
            elif ext in [".png", ".jpg", ".jpeg", ".gif"] and para_idx == 0 and img_idx == 0:
                return StreamingResponse(open(path, "rb"), media_type=f"image/{ext.strip('.')}")
        raise HTTPException(status_code=404, detail="Image not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

# === LINKS ENDPOINT ===
@app.get("/links")
def get_links(file: str = Query(...), idx: int = Query(...)):
    try:
        path = os.path.join(DOCUMENTS_FOLDER, file)
        triplets = extract_text_image_link_pairs(path)
        if idx >= len(triplets):
            raise HTTPException(status_code=404, detail="Index out of bounds")
        _, _, links = triplets[idx]
        return {"links": links}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error retrieving links")

# === FILE UPLOAD ===
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        save_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        ext = os.path.splitext(file.filename)[1].lower()
        image_ids = []
        if ext == ".docx":
            triplets = extract_text_image_link_pairs(save_path)
            for para_idx, (_, imgs, _) in enumerate(triplets):
                for img_idx, _ in enumerate(imgs):
                    image_ids.append(f"{file.filename}::img{para_idx}_{img_idx}")
        elif ext in [".png", ".jpg", ".jpeg", ".gif"]:
            image_ids.append(f"{file.filename}::img0_0")
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
        ingest()
        global chain, retriever, llm
        chain, retriever, llm = build_chain()
        return {
            "filename": file.filename,
            "message": "File uploaded successfully",
            "image_ids": image_ids,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# === FEEDBACK ===
FEEDBACK_FILE = "feedback_log.json"

@app.post("/feedback")
def collect_feedback(data: FeedbackRequest):
    entry = data.dict()
    if not os.path.exists(FEEDBACK_FILE):
        with open(FEEDBACK_FILE, "w") as f:
            json.dump([], f)
    with open(FEEDBACK_FILE, "r+") as f:
        try:
            logs = json.load(f)
        except json.JSONDecodeError:
            logs = []
        logs.append(entry)
        f.seek(0)
        json.dump(logs, f, indent=2)
    return {"status": "success", "message": "Feedback recorded"}

# === SUGGESTIONS ===
suggestion_prompt = PromptTemplate.from_template(
    "Suggest 2-3 user questions based on the following document:\n\n{context}\n\nEach question on a new line starting with '-'."
)
suggest_chain: Runnable = suggestion_prompt | llm | StrOutputParser()

@app.get("/suggest", response_model=List[str])
def get_suggestions(q: str = Query(..., min_length=2)):
    try:
        retriever.search_kwargs.update({"k": 2})
        docs = retriever.invoke(q)
        if not docs:
            return ["What is this document about?", "Can you summarize this?", "Is this relevant to my query?"]
        context_text = "\n\n".join(doc.page_content[:500] for doc in docs)[:2000]
        raw_output = suggest_chain.invoke({"context": context_text})
        lines = raw_output.strip().splitlines()
        suggestions = [
            re.sub(r"^\d+[\.\)]\s*", "", line.strip("-•").strip())
            for line in lines
            if line.strip().startswith(("-", "•", "1", "2", "3"))
        ]
        if not suggestions:
            suggestions = [
                s.strip() + "?"
                for s in raw_output.split("?")
                if len(s.strip()) > 5
            ][:3]
        return suggestions[:3]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate suggestions")


@app.post("/chatProcessFlow")
def chatProcessFlow(req: SessionFetchRequest):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # if user exists
        cursor.execute("SELECT ID FROM USER_SETTINGS_TABLE WHERE EMAIL_ID = ?", (req.email,))
        existing_user = cursor.fetchone()

        if not existing_user:
            # Insert user
            cursor.execute("""
                INSERT INTO CHAT_PROCESS_FLOW_TABLE (CHAT_PROCESS_NAME, CREATED_BY)
                VALUES (?, ?)
            """, (req.chatProcessName, req.username))

        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.get("/getChatProcessFlow")
def getChatProcessFlow():
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Fetch all chat process flows
        cursor.execute("SELECT CHAT_PROCESS_NAME FROM CHAT_PROCESS_FLOW_TABLE")
        chat_process_flows = cursor.fetchall()
        chat_process_list = [row[0] for row in chat_process_flows]
        if not chat_process_list:
            raise HTTPException(status_code=404, detail="No chat process flows found")
        print(f"Chat process flows found: {chat_process_list}")
        return {"chat_process_flows": chat_process_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor: cursor.close()
        if conn: conn.close()