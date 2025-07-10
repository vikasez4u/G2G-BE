import os
from io import BytesIO
from PIL import Image
from docx import Document
from langchain_ollama import ChatOllama
from langchain_chroma import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.prompts import PromptTemplate
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain.docstore.document import Document as LCDocument
from xml.etree.ElementTree import tostring
from functools import lru_cache

#import streamlit as st

CHROMA_DB_DIR = "./sql_chroma_db"
DOCUMENTS_FOLDER = "./documents"

embedder = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
vector_store = Chroma(persist_directory="CHROMA_DB_DIR", embedding_function=embedder)
retriever = vector_store.as_retriever(search_type="mmr", search_kwargs={"k": 3, "fetch_k": 6, "lambda_mult": 0.8})

def extract_text_image_link_pairs(doc_path):
    from lxml import etree
    etree.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
    etree.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

    doc = Document(doc_path)
    text_chunks, image_chunks, link_chunks = [], [], []
    rels = doc.part.rels
    images = {}

    for rel in rels.values():
        if "image" in rel.target_ref:
            try:
                image_data = rel.target_part.blob
                img = Image.open(BytesIO(image_data)).convert("RGB")
                images[rel.rId] = img
            except Exception as e:
                print("Image error:", e)

    for para in doc.paragraphs:
        para_text = para.text.strip()
        text_chunks.append(para_text)

        inline_images = []
        if para._element.xpath(".//w:drawing"):
            for drawing in para._element.xpath(".//w:drawing"):
                # embed_id = drawing.xpath(".//a:blip/@r:embed")
                drawing_xml = etree.fromstring(tostring(drawing))
                embed_id = drawing_xml.xpath(
                    ".//a:blip/@r:embed",
                    namespaces={
                        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                    }
                )

                for rId in embed_id:
                    if rId in images:
                        inline_images.append(images[rId])
        image_chunks.append(inline_images)

        para_links = []
        for hyperlink in para._element.xpath(".//w:hyperlink"):
            rId = hyperlink.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            if rId and rId in rels:
                para_links.append(rels[rId].target_ref)
        link_chunks.append(para_links)

    for i, para in enumerate(doc.paragraphs):
        drawings = para._element.xpath(".//w:drawing")


    return list(zip(text_chunks, image_chunks, link_chunks))

def ingest(): 
    if os.path.exists(CHROMA_DB_DIR):
        print("Chroma DB already exists, skipping ingestion.")
        return

    print("Starting ingestion...")

    all_chunks = []
    
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=100)

    for filename in os.listdir(DOCUMENTS_FOLDER):
        if filename.endswith(".docx"):
            path = os.path.join(DOCUMENTS_FOLDER, filename)
            para_triplets = extract_text_image_link_pairs(path)
            for i, (para_text, _, _) in enumerate(para_triplets):
                if len(para_text.strip()) < 30:
                    continue
                splits = splitter.split_text(para_text)
                for c in splits:
                    all_chunks.append(LCDocument(
                        page_content=c,
                        metadata={"source": filename, "para_index": i}
                    ))
    print(f"📄 Total chunks being ingested: {len(all_chunks)}")
    db = Chroma.from_documents(all_chunks, embedding=embedder, persist_directory=CHROMA_DB_DIR)
    db.persist()
    print("✅ Chroma DB created and persisted.")



def build_chain():
    print("Building Chain")
    model = ChatOllama(model="llama3.2", temperature=0.4,
                       options={
            "num_predict": 200,
            "top_p": 0.9,
            "repeat_penalty": 1.1
        }
)
    prompt = PromptTemplate.from_template("""
SYSTEM ROLE:
You are a professional and friendly virtual assistant for Accenture.
Your tone is warm, formal, and helpful.

BEHAVIOR RULES:
- Greet only once at the beginning of a new conversation with “Hello.”
- Avoid repeated greetings or thanks mid-chat.
- Use markdown formatting in all responses.
- Use emojis only when they enhance clarity or warmth.
- Use bullet points to list items or steps.
- Use section headings for clarity.
- Use short paragraphs for readability.
- Use concise and clear language.
- Base every response strictly on provided documents.
- If documents has image or link for that question, include them in the response.
- If the answer is not in the documents, reply with: “Reach out to Respective POCs.”
- Offer help proactively only at the beginning of a new conversation.
- End every response with a polite thank-you and positive closing.

CONDITIONAL LOGIC:
Respond based only on the user's query and provided documents.

- If the user asks about:
    - “leave balance”
    - “leave history”
    - “leave calculation”
    - “leave entitlements”
→ Provide entitlement breakdown or usage summary from documents.

- If the user asks about:
    - “leave policy”
    - “leave types”
    - “leave guidelines”
    - “leave FAQs”
→ Summarize relevant policies only from documented content.

- If the user asks about:
    - “leave application”
    - “leave status”
    - “leave request”
    - “leave approvals”
    - “leave process”
→ Guide through documented application process and workflows.

- If the user asks about:
    - “leave system”
    - “leave notifications”
    - “leave reminders”
    - “leave assistance”
    - “leave support”
    - “leave updates”
→ Describe documented notification pathways or support steps.

- If the user asks about:
    - “leave documentation”
    - “leave forms”
    - “leave procedures”
    - “leave deadlines”
→ Share required forms, timelines, and procedural steps per documentation.

- If the user asks about:
    - “leave queries”
    - “leave resources”
    - “leave contacts”
    - “leave feedback”
    - “leave issues”
→ Direct to relevant contacts or escalate using: “Reach out to Respective POCs.”

FALLBACK RULE:
- If the query does not match any condition or information is missing from the documents:
→ Reply with: “Please clarify your question or Reach out to Respective POCs.”

Input:
{input}

Context:
{context}

Answer:
""")

    retriever = vector_store.as_retriever(search_type="mmr", search_kwargs={"k": 3, "fetch_k": 6, "lambda_mult": 0.8})
    doc_chain = create_stuff_documents_chain(model, prompt)
    # return create_retrieval_chain(retriever, doc_chain), retriever
    return create_retrieval_chain(retriever, doc_chain), retriever, model

@lru_cache(maxsize=128)
def cached_retrieve(user_input: str):
    return retriever.get_relevant_documents(user_input)


ingest()
chat_chain, chat_retriever,llm = build_chain()

#query = st.text_input("Ask a question:")
#if query:
#    chat_chain, chat_retriever,llm = build_chain()
#    response = chat_chain.invoke({"input": query})
#    st.write("### Answer")
#    st.write(response)
