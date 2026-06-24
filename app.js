const views = [...document.querySelectorAll(".view")];
const navItems = [...document.querySelectorAll(".nav-item")];
const title = document.querySelector("#page-title");
const toast = document.querySelector("#toast");
let currentModelLabel = "selected model";

async function refreshModelLabel() {
  try {
    const response = await fetch("http://127.0.0.1:8000/api/model/health");
    if (!response.ok) return;
    const data = await response.json();
    currentModelLabel = data.model || currentModelLabel;
    currentModelLabel = data.model || currentModelLabel;
  } catch (error) {
    // Keep the generic label when the backend is not running yet.
  }
}

refreshModelLabel();

const titles = {
  dashboard: "Good afternoon, Thuan",
  tutor: "Learn with your AI tutor",
  assessment: "Practice at your level",
  plan: "Your study plan"
};

function showView(id) {
  views.forEach((view) => view.classList.toggle("active", view.id === id));
  navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === id));
  title.textContent = titles[id];
}

navItems.forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
document.querySelector("[data-open-tutor]").addEventListener("click", () => showView("tutor"));
document.querySelector("[data-view-button]").addEventListener("click", () => showView("plan"));
document.querySelector("#quiz-from-chat").addEventListener("click", () => showView("assessment"));

function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2300);
}

const responses = [
  {
    keywords: ["simple", "explain", "what"],
    text: "Integration by parts reverses the product rule. Choose one factor to differentiate (u) and the other to integrate (dv), then apply âˆ«u dv = uv âˆ’ âˆ«v du. A useful selection guide is LIATE: logarithmic, inverse trig, algebraic, trig, exponential.",
    source: "[1] Lecture 08, pp. 12â€“13"
  },
  {
    keywords: ["example", "show", "work"],
    text: "For âˆ«x cos(x) dx, choose u = x and dv = cos(x)dx. Then du = dx and v = sin(x). Substitution gives x sin(x) âˆ’ âˆ«sin(x)dx = x sin(x) + cos(x) + C.",
    source: "[2] Calculus Textbook, Ch. 7.1"
  },
  {
    keywords: ["hint", "try"],
    text: "Hint first: identify which part becomes simpler when differentiated. In âˆ«x eË£ dx, differentiating x gives 1, so x is a strong choice for u. What should dv be?",
    source: "[1] Lecture 08, p. 14"
  }
];

function tutorReply(input) {
  const lower = input.toLowerCase();
  return responses.find((item) => item.keywords.some((word) => lower.includes(word))) || {
    text: "Let us break it into three moves: choose u and dv, compute du and v, then substitute into the formula. For your current weak topic, I recommend starting with a hint before viewing the full solution.",
    source: "[1] Lecture 08, pp. 12-15"
  };
}

async function askBackendTutor(input, onToken) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 25000);

  try {
    const response = await fetch("http://127.0.0.1:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: `${input}\n\nTra loi that ngan gon, toi da 4 cau.`,
        course: "Calculus II",
        topic: "Integration by parts"
      })
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    currentModelLabel = data.model || currentModelLabel;
    const modelLabel = data.model ? `${data.model} via Ollama` : "Ollama model";
    const sourceLabel = data.citations?.length
      ? `${data.citations.map((item) => `[${item.sourceId}] ${item.title}, p. ${item.page}`).join("; ")} | ${modelLabel}`
      : modelLabel;

    onToken(data.answer || "No answer returned from model.");
    return {
      text: data.answer || "No answer returned from model.",
      source: sourceLabel,
      model: data.model || "Ollama"
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

function addMessage(text, kind, source) {
  const message = document.createElement("div");
  message.className = `message ${kind === "user" ? "user-message" : "tutor-message"}`;
  if (kind === "user") {
    message.innerHTML = `<div><p></p></div>`;
  } else {
    message.innerHTML = `<span class="bot-avatar">AI</span><div><p></p><button class="citation"></button></div>`;
    message.querySelector(".citation").textContent = source;
  }
  message.querySelector("p").textContent = text;
  document.querySelector("#messages").appendChild(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
}

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#chat-text");
  const text = input.value.trim();
  if (!text) return;
  addMessage(text, "user");
  input.value = "";
  addMessage(`Thinking with ${currentModelLabel}...`, "tutor", "Backend / Ollama");

  const messages = document.querySelector("#messages");
  const pendingMessage = messages.lastElementChild;

  try {
    const reply = await askBackendTutor(text, (partial) => { pendingMessage.querySelector("p").textContent = partial || `Thinking with ${currentModelLabel}...`; });
    pendingMessage.querySelector("p").textContent = reply.text;
    pendingMessage.querySelector(".citation").textContent = reply.source;
  } catch (error) {
    const fallback = tutorReply(text);
    pendingMessage.querySelector("p").textContent = `${fallback.text} (Fallback because backend is not available yet.)`;
    pendingMessage.querySelector(".citation").textContent = fallback.source;
    notify("Backend unavailable - using fallback tutor response");
  }
});

document.querySelectorAll(".quick-prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelector("#chat-text").value = button.textContent;
    document.querySelector("#chat-form").requestSubmit();
  });
});

const questions = [
  {
    level: "Level 1 Â· Foundation",
    question: "For âˆ«x cos(x) dx, which choice correctly assigns u and dv?",
    answers: ["u = cos(x), dv = x dx", "u = x, dv = cos(x) dx", "u = x cos(x), dv = dx", "u = 1, dv = x cos(x) dx"],
    correct: 1,
    explanation: "Correct: choose the algebraic factor x as u because differentiation simplifies it. Then v = sin(x)."
  },
  {
    level: "Level 2 Â· Apply",
    question: "What is the result of âˆ«x eË£ dx?",
    answers: ["xeË£ âˆ’ eË£ + C", "xeË£ + eË£ + C", "xÂ²eË£/2 + C", "eË£ + C"],
    correct: 0,
    explanation: "Using u = x and dv = eË£dx gives xeË£ âˆ’ âˆ«eË£dx = xeË£ âˆ’ eË£ + C."
  },
  {
    level: "Level 3 Â· Transfer",
    question: "Which integral requires applying integration by parts twice?",
    answers: ["âˆ«xÂ²eË£ dx", "âˆ«sin(x) dx", "âˆ«2x dx", "âˆ«1/x dx"],
    correct: 0,
    explanation: "For âˆ«xÂ²eË£dx, differentiating xÂ² once still leaves 2x, so integration by parts is applied again."
  }
];
let questionIndex = 0;

function renderQuestion() {
  const question = questions[questionIndex];
  document.querySelector("#difficulty").textContent = question.level;
  document.querySelector("#question-number").textContent = questionIndex + 1;
  document.querySelector("#quiz-question").textContent = question.question;
  const answers = document.querySelector("#answers");
  answers.innerHTML = "";
  question.answers.forEach((answer, index) => {
    const button = document.createElement("button");
    button.textContent = answer;
    button.addEventListener("click", () => checkAnswer(button, index));
    answers.appendChild(button);
  });
  document.querySelector("#feedback").classList.add("hidden");
  document.querySelector("#next-question").classList.add("hidden");
}

function checkAnswer(button, selected) {
  const question = questions[questionIndex];
  const allAnswers = [...document.querySelectorAll("#answers button")];
  allAnswers.forEach((item) => item.disabled = true);
  const correct = selected === question.correct;
  button.classList.add(correct ? "selected-correct" : "selected-wrong");
  if (!correct) allAnswers[question.correct].classList.add("selected-correct");
  const feedback = document.querySelector("#feedback");
  feedback.textContent = `${correct ? "Well done. " : "Not quite. "}${question.explanation}`;
  feedback.classList.remove("hidden");
  document.querySelector("#next-question").classList.remove("hidden");
  if (correct) {
    const confidence = Math.min(100, 46 + ((questionIndex + 1) * 8));
    document.querySelector("#confidence").textContent = `${confidence}%`;
    document.querySelector("#confidence-bar").style.width = `${confidence}%`;
  }
}

document.querySelector("#next-question").addEventListener("click", () => {
  if (questionIndex < questions.length - 1) {
    questionIndex += 1;
    renderQuestion();
  } else {
    notify("Assessment complete Â· Topic confidence updated");
    showView("dashboard");
  }
});

document.querySelector("#reset-demo").addEventListener("click", () => {
  questionIndex = 0;
  renderQuestion();
  document.querySelector("#confidence").textContent = "46%";
  document.querySelector("#confidence-bar").style.width = "46%";
  notify("Demo data reset");
  showView("dashboard");
});

renderQuestion();








