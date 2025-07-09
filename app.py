import os
from io import BytesIO
from PIL import Image
from docx import Document
from langchain_ollama import ChatOllama
from langchain_community.vectorstores import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.prompts import PromptTemplate
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from langchain.docstore.document import Document as LCDocument
from xml.etree.ElementTree import tostring


CHROMA_DB_DIR = "./sql_chroma_db"
DOCUMENTS_FOLDER = "./documents"

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
    embedder = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")

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
    model = ChatOllama(model="llama3", temperature=0.4)
    embedder = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    prompt = PromptTemplate.from_template("""
You are a friendly and helpful virtual assistant for Accenture.
Your tone must be warm, professional, and formal at all times.
Behavior Rules:
Greet the user only at the start of the first conversation with a friendly tone. Use "Hello" only once.
During the conversation:
Do not repeat greetings (e.g., no "hello" or "thank you" mid-chat).
Answer questions accurately, concisely, and contextually.
Stick strictly to the user's question and context.
Use bullet points when the answer involves multiple parts.
Give detailed explanations only if the user asks for them.
Use emojis sparingly to remain warm yet professional.
Provide links or images only if directly relevant to the question.
Offer help proactively if it’s the user’s first message or beginning of a conversation.
End the conversation with a polite thank-you and positive closing.

Input:
{input}

Context:
{context}

Answer:
""")

    vector_store = Chroma(persist_directory=CHROMA_DB_DIR, embedding_function=embedder)
    retriever = vector_store.as_retriever(search_type="mmr", search_kwargs={"k": 3, "fetch_k": 6, "lambda_mult": 0.8})
    doc_chain = create_stuff_documents_chain(model, prompt)
    # return create_retrieval_chain(retriever, doc_chain), retriever
    return create_retrieval_chain(retriever, doc_chain), retriever, model

ingest()
chat_chain, chat_retriever,llm = build_chain()


