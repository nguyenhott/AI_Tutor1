# Backend Demo - Personal AI Tutoring Tool
Backend này nằm trong folder demo và dùng FastAPI để gọi model local qua Ollama.
Model mặc định cho laptop demo: qwen2.5:1.5b
Model thứ hai để so sánh/test:  gemma2:2b
1. Pull model lần đầu
ollama pull qwen2.5:1.5b
ollama pull gemma2:2b
2. Test model trực tiếp bằng Ollama
Test Qwen:
ollama run qwen2.5:1.5b
Test Gemma:
ollama run gemma2:2b
Câu hỏi mẫu:
Integration by parts là gì? Trả lời ngắn gọn.
Giải thích công thức ∫u dv = uv - ∫v du.
Hãy giải bài ∫x cos(x) dx ngắn gọn.
Cho ví dụ từng bước với tích phân x cos(x) dx.
Dựa trên tài liệu đã cho, hãy giải thích integration by parts và ghi nguồn.
Thoát khỏi Ollama chat:
/bye

3. Chạy backend với Qwen mặc định
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000

Mở link kiểm tra: http://127.0.0.1:8000/api/model/health
Kết quả nên có:"model": "qwen2.5:1.5b"

4. Chạy backend với Gemma
Nếu muốn đổi backend sang model Gemma để test, chạy:
cd D:\Project\Personal_AI_Tutoring_Tool\demo\backend
$env:OLLAMA_MODEL="gemma2:2b"
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Sau đó mở: http://127.0.0.1:8000/api/model/health
Kết quả nên có: "model": "gemma2:2b"

Lưu ý: biến `$env:OLLAMA_MODEL` chỉ có hiệu lực trong terminal hiện tại. Nếu mở terminal mới, backend sẽ quay về model mặc định trong code hoặc `.env`.

5. Test API chat
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"Tra loi ngan gon: integration by parts la gi?","course":"Calculus II","topic":"Integration by parts"}'
Nếu API chạy đúng, kết quả sẽ có các trường chính:
{
  "answer": "...",
  "citations": ["S1", "S2"],
  "model": "gemma2:2b"
}
Trường `"model"` sẽ là `qwen2.5:1.5b` hoặc `gemma2:2b` tùy model đang chạy.
6. Chạy frontend demo
cd D:\Project\Personal_AI_Tutoring_Tool\demo
npm start:  http://localhost:3000


Khi backend đang chạy với Gemma, web demo cũng sẽ nhận câu trả lời từ Gemma.
