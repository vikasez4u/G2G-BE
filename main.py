from fastapi import FastAPI, Query, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime
from io import BytesIO
from typing import List
import os
import uuid
import json
import shutil
import re
import mysql.connector

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_community.chat_models import ChatOllama

from app import build_chain, extract_text_image_link_pairs, DOCUMENTS_FOLDER

# === INIT ===
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://g2g-chatbot-geakehf4aqamfcfb.eastasia-01.azurewebsites.net"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "./uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

chain, retriever, llm = build_chain()

# === MODELS ===
class QueryRequest(BaseModel):
    question: str

class FeedbackRequest(BaseModel):
    question: str
    response: str
    feedback: str
    session_id: str = str(uuid.uuid4())
    timestamp: str = datetime.utcnow().isoformat()

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
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="accenture",
        database="your_database"
    )

# === Checking app ===
@app.get("/test")
def test():
    return "Welcome to Guide 2 Govern Application"

# === CHAT ===
@app.post("/chat")
def chat(req: QueryRequest):
    result = chain.invoke({"input": req.question})
    relevant_docs = retriever.invoke(req.question)

    image_ids = []
    related_links = set()
    seen_ids = set()

    for doc in relevant_docs:
        fname = doc.metadata.get("source")
        para_idx = doc.metadata.get("para_index")
        if fname is None or para_idx is None:
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

    return {
        "answer": result["answer"],
        "image_ids": image_ids,
        "related_links": list(related_links)
    }

# === SIGNIN ===
@app.post("/signin")
def signin(user: SignInRequest):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT IGNORE INTO users (email, username) VALUES (%s, %s)", (user.email, user.username))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# === SAVE MESSAGE ===
@app.post("/save_message")
def save_message(msg: MessageSaveRequest):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        created_at = datetime.fromisoformat(msg.created_at.replace("Z", ""))
        cursor.execute("""
            INSERT INTO messages (session_id, email, sender, text, created_at, image_ids, related_links)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            msg.session_id, msg.email, msg.sender, msg.text, created_at,
            json.dumps(msg.image_ids), json.dumps(msg.related_links)
        ))
        conn.commit()
        return {"status": "saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# === GET SESSION MESSAGES ===
@app.post("/get_session")
def get_session(req: SessionFetchRequest):
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT sender, text, created_at, image_ids, related_links FROM messages
            WHERE session_id = %s AND email = %s ORDER BY created_at ASC
        """, (req.session_id, req.email))
        results = cursor.fetchall()
        for msg in results:
            msg["image_ids"] = json.loads(msg["image_ids"] or "[]")
            msg["related_links"] = json.loads(msg["related_links"] or "[]")
        return {"messages": results, "session_id": req.session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# === GET HISTORY ===
@app.get("/get_history")
def get_history(email: str = Query(...)):
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT session_id, MAX(created_at) as last_time FROM messages
            WHERE email = %s GROUP BY session_id ORDER BY last_time DESC
        """, (email,))
        sessions = cursor.fetchall()
        history = []
        for s in sessions:
            cursor.execute("""
                SELECT text FROM messages
                WHERE email = %s AND session_id = %s AND sender = 'user'
                ORDER BY created_at ASC LIMIT 1
            """, (email, s['session_id']))
            first_msg = cursor.fetchone()
            if first_msg:
                history.append({
                    "session_id": s['session_id'],
                    "first_message": first_msg['text']
                })
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

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
