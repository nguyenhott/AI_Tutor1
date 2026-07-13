# Backend - Personal AI Tutoring Tool

Backend dùng FastAPI để cung cấp các chức năng chính cho Personal AI Tutoring Tool:

- Chat với AI Tutor qua Ollama.
- Upload tài liệu môn học từ web.
- Extract text từ PDF/TXT/MD.
- Chunk tài liệu.
- Tạo embedding bằng Ollama embedding model.
- Lưu vector vào ChromaDB.
- Retrieve tài liệu bằng Chroma vector search.
- Fallback về JSON/keyword retrieval nếu Chroma hoặc embedding chưa sẵn sàng.
- Lưu chat history, quiz attempts, và topic mastery bằng SQLite.
- Sinh adaptive practice theo tài liệu đã chọn, topic, độ khó, số câu, và loại câu.
- Chấm multiple choice và short answer dựa trên tài liệu upload/RAG.

## 1. Models Cần Pull

Chat model chính:

```powershell
ollama pull qwen2.5:1.5b
```

Model so sánh:

```powershell
ollama pull gemma2:2b
```

Embedding model cho RAG:

```powershell
ollama pull nomic-embed-text
```

## 2. Cài Dependency Backend

```powershell
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Dependency quan trọng:

- `fastapi`: backend API.
- `pypdf`: đọc PDF.
- `python-multipart`: nhận upload file.
- `httpx`: gọi Ollama.
- `chromadb`: vector database cho RAG.

## 3. Cấu Hình

`.env.example`:

```text
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_NUM_PREDICT=700
OLLAMA_NUM_CTX=2048
OLLAMA_EMBED_BATCH_SIZE=16
CHROMA_DB_DIR=./data/chroma
CHROMA_COLLECTION=course_materials
```

Có thể đổi chat model:

```powershell
$env:OLLAMA_MODEL="gemma2:2b"
```

## 4. Chạy Backend

```powershell
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Restart backend khi sửa code Python hoặc backend bị lỗi:

```powershell
Ctrl + C
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Kiểm tra backend đã chạy:

```text
http://127.0.0.1:8000/health
```

Nếu thấy `{"status":"ok"}` là backend đã sẵn sàng.

Kiểm tra model/RAG:

```text
http://127.0.0.1:8000/api/model/health
http://127.0.0.1:8000/api/rag/status
```

Nếu Chroma đang có vector, status sẽ có:

```json
"retrievalMode": "chroma"
```

## 5. Chạy Frontend Và Upload Tài Liệu

Với textbook lớn, nên nhập keywords hẹp để giảm số trang cần index. Nếu dùng keyword quá rộng như circuit, hệ thống có thể giữ gần như cả cuốn sách và thời gian embedding sẽ rất lâu.

Embedding được xử lý theo batch nhỏ qua OLLAMA_EMBED_BATCH_SIZE để tránh request quá lớn sang Ollama.


Chạy frontend:

```powershell
cd D:\Project\Personal_AI_Tutoring_Tool\demo
npm start
```

Mở:

```text
http://localhost:3000
```

Vào màn hình **Knowledge**, dùng form **Upload course material** để upload:

- `.pdf`
- `.txt`
- `.md`

Flow xử lý:

```text
upload file
-> save into backend/uploads
-> extract text
-> split into chunks
-> call Ollama embedding model
-> store vectors into ChromaDB
-> store readable backup into data/rag_index.json
```

Sau khi upload xong:

```text
Knowledge -> chọn Practice trên tài liệu
-> màn Practice tự chọn tài liệu đó
-> nhập/sửa Topic
-> chọn số câu, độ khó, loại câu
-> Generate practice
```

## 6. API Upload Tài Liệu

Endpoint:

```text
POST /api/documents/upload
```

Ví dụ PowerShell:

```powershell
$form = @{
  file = Get-Item "D:\Dai hoc\Calculus\Main textbook\Early_TranscEndEnTals_EighTh_EdiTion.pdf"
  title = "Calculus Early Transcendentals"
  keywords = "integration by parts,partial fractions,trigonometric substitution"
}
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/documents/upload" -Method Post -Form $form
```

`keywords` là optional. Nếu bỏ keywords, backend sẽ index rộng hơn toàn bộ tài liệu.

## 7. Build Index Bằng Script

Script vẫn tên `build_calculus_index.py` để giữ workflow cũ, nhưng hiện hỗ trợ PDF/TXT/MD bất kỳ và ghi vector vào Chroma.

Build index có embedding + Chroma:

```powershell
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
.\.venv\Scripts\python .\scripts\build_calculus_index.py `
  --file "D:\Dai hoc\Calculus\Main textbook\Early_TranscEndEnTals_EighTh_EdiTion.pdf" `
  --title "Calculus: Early Transcendentals, 8th Edition"
```

Build theo keyword để nhanh hơn:

```powershell
.\.venv\Scripts\python .\scripts\build_calculus_index.py `
  --file "D:\Dai hoc\Calculus\Main textbook\Early_TranscEndEnTals_EighTh_EdiTion.pdf" `
  --title "Calculus: Early Transcendentals, 8th Edition" `
  --keywords "integration by parts,partial fractions"
```

Build không embedding, chỉ chunk text và dùng fallback keyword retrieval:

```powershell
.\.venv\Scripts\python .\scripts\build_calculus_index.py --no-embedding
```

## 8. Kiểm Tra Documents Và Chroma

List tài liệu đã index:

```text
http://127.0.0.1:8000/api/documents
```

RAG status:

```text
http://127.0.0.1:8000/api/rag/status
```

Ví dụ status tốt:

```json
{
  "retrievalMode": "chroma",
  "indexedChunks": 1200,
  "embeddedChunks": 1200,
  "vectorStore": {
    "path": ".../data/chroma",
    "collection": "course_materials",
    "vectors": 1200
  }
}
```

## 9. Chat API Có RAG Và History

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"Integration by parts la gi? Giai thich ngan gon.","course":"Calculus II","topic":"Integration by parts"}'
```

Response gồm:

```json
{
  "answer": "...",
  "citations": [...],
  "model": "qwen2.5:1.5b",
  "retrievedSources": [
    {
      "sourceId": "S1",
      "title": "Calculus: Early Transcendentals, 8th Edition",
      "page": 504,
      "score": 0.72
    }
  ]
  "sessionId": "..."
}
```

Chat được lưu vào SQLite:

```text
GET /api/chat/sessions
GET /api/chat/sessions/{session_id}/messages
```

## 10. RAG Architecture Hiện Tại

```text
Frontend upload
-> POST /api/documents/upload
-> document_indexer.py extracts text
-> chunks are created
-> ollama_client.py calls nomic-embed-text
-> vector_store.py writes vectors to ChromaDB

User asks question
-> POST /api/chat
-> sources.py embeds query
-> ChromaDB retrieves top-k chunks
-> main.py builds prompt with SOURCES
-> ollama_client.py sends prompt to chat model
-> answer + citations returned to frontend
-> storage.py saves chat session/messages

User generates practice
-> choose Material + Topic + Count + Difficulty + Type
-> POST /api/practice/recommend with documentId
-> sources.py retrieves chunks from the selected document
-> assessment.py asks Ollama for JSON quiz questions
-> backend validates questions
-> if LLM JSON is invalid, backend creates fallback questions from retrieved source text

User submits quiz answer
-> POST /api/practice/submit
-> multiple_choice is graded by correctAnswer
-> short_answer is evaluated by LLM with source-grounded rubric
-> storage.py saves quiz attempt and updates topic mastery
```

## 11. Files Quan Trọng

```text
backend/app/vector_store.py        # ChromaDB persistent vector store
backend/app/document_indexer.py    # ingest documents, chunk, embed, save index/vector
backend/app/sources.py             # Chroma retrieval and fallback retrieval
backend/app/main.py                # upload/chat/status APIs
backend/app/ollama_client.py       # chat model and embedding model calls
backend/app/assessment.py          # practice generation and answer evaluation
backend/app/storage.py             # SQLite chat/quiz/mastery persistence
backend/scripts/build_calculus_index.py # CLI index builder
backend/data/chroma/               # Chroma persistent vector database
backend/data/rag_index.json        # readable backup index
backend/data/tutorflow.sqlite      # chat history, quiz attempts, mastery
backend/uploads/                   # uploaded source documents
```


## 12. Adaptive Practice / Assessment Agent

Backend hiện có Practice API để sinh câu hỏi luyện tập theo tài liệu và topic người dùng chọn.
Phiên bản hiện tại dùng hướng project hoàn thiện hơn: `document selection -> RAG retrieval -> LLM quiz generation -> backend validation/fallback -> grading`.

Luồng generate câu hỏi:

```text
documentId + topic + student prompt + mastery + count + difficulty + questionType
-> retrieve_sources() lấy đoạn tài liệu liên quan từ đúng document trong Chroma/RAG
-> generate_practice_with_llm() gửi context cho Ollama
-> LLM trả JSON questions
-> backend validate/normalize thành PracticeQuestion
-> nếu LLM trả JSON lỗi, backend tự tạo fallback questions từ source retrieved
```

Recommend adaptive questions:

```text
POST /api/practice/recommend
```

Ví dụ:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/practice/recommend" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"course":"Electric Circuits","topic":"Ohm''s law","prompt":"Ohm law la gi?","documentId":"doc-book-electric-circuits-9th-ed-j-nilsson--f87354f721","mastery":45,"count":4,"difficulty":"easy","questionType":"mixed"}'
```

Submit answer:

```text
POST /api/practice/submit
```

Practice hiện hỗ trợ:

- `llm_rag`: sinh quiz động từ tài liệu đã upload/index.
- `fallback_rag`: tự sinh câu hỏi từ retrieved source text nếu LLM không trả JSON hợp lệ.
- `multiple_choice`: chấm bằng đáp án đúng.
- `short_answer`: gọi AI evaluate bằng prompt riêng, dựa trên source material + rubric keywords.
- `documentId`: giới hạn retrieval và assessment trong tài liệu được chọn.
- adaptive level dựa trên mastery score:
  - dưới 50: Foundation
  - 50-74: Apply
  - từ 75: Transfer

Frontend flow:

```text
User uploads/selects a course material
-> user enters Topic in Practice
-> user chooses question count, difficulty, and type
-> frontend calls /api/practice/recommend with documentId
-> backend retrieves RAG sources from that document and asks LLM to generate adaptive questions
-> user answers
-> frontend calls /api/practice/submit
-> backend returns feedback and updated mastery
```

## 13. Database / Persistence

SQLite database:

```text
backend/data/tutorflow.sqlite
```

Tables:

```text
chat_sessions   # topic/course/title per chat
chat_messages   # user/assistant messages + metadata
quiz_attempts   # question, answer, result, score
topic_mastery   # mastery score per course/topic
```

APIs:

```text
GET /api/chat/sessions
GET /api/chat/sessions/{session_id}/messages
GET /api/practice/attempts
```

## 14. Current Demo Scope And Future Work

Đã làm trong demo:

```text
Upload material -> RAG/Chroma -> Tutor Chat -> Practice Generation -> Answer Evaluation -> SQLite History
```

Chưa làm đầy đủ so với bản mô tả production:

```text
Progress Monitor Agent thật
Dynamic Study Planner thật
LMS integration OAuth
Calendar integration
Email/push notification
Multi-user auth and role-based access
Production encryption/scaling/uptime guarantees
```

Hướng tiếp theo hợp lý:

```text
1. Tạo Progress Monitor đọc quiz_attempts/topic_mastery để phát hiện weak topics.
2. Tạo Study Plan động từ weak topics + deadline mock data.
3. Nối dashboard với weak-topic alert thật.
4. Cập nhật report/slides theo architecture hiện tại.
```
