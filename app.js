const views = [...document.querySelectorAll(".view")];
const navItems = [...document.querySelectorAll(".nav-item")];
const title = document.querySelector("#page-title");
const toast = document.querySelector("#toast");
let currentModelLabel = "selected model";
const LOCAL_BACKEND = "http://127.0.0.1:8000";
const API_BASE = ["localhost", "127.0.0.1"].includes(window.location.hostname) && window.location.port !== "8000"
  ? LOCAL_BACKEND
  : window.location.origin;
let availableDocuments = [];
let availableCourses = [];
let currentCourseId = "course-c-programming";
let currentPracticeTopic = "Pointers in C";
let currentPracticeCourse = "C Programming";
let currentPracticePrompt = "Pointers in C";
let currentPracticeDocumentId = "";
let currentMastery = 46;
let currentSessionId = null;
let latestProgressReport = null;
let latestStudyPlan = null;
let currentUser = null;
let googleSignInReady = false;

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.textContent = value;
}

function courseQuery() {
  return currentPracticeCourse ? `?course=${encodeURIComponent(currentPracticeCourse)}` : "";
}

function courseByName(name) {
  return availableCourses.find((course) => course.name === name);
}

function renderCoursePicker(courses = []) {
  const picker = document.querySelector("#course-picker");
  if (!picker) return;
  picker.innerHTML = courses.map((course) => `
    <button class="course-card-button ${course.id === currentCourseId ? "active" : ""}"
      data-course-id="${escapeHtml(course.id)}"
      data-course="${escapeHtml(course.name)}"
      data-topic="${escapeHtml(course.default_topic || course.defaultTopic || "General review")}">
      <strong>${escapeHtml(course.name)}</strong>
      <small>${escapeHtml(course.description || "Personal course workspace")}</small>
    </button>
  `).join("");

  picker.querySelectorAll(".course-card-button").forEach((button) => {
    button.addEventListener("click", () => selectCourse({
      id: button.dataset.courseId,
      name: button.dataset.course,
      default_topic: button.dataset.topic
    }));
  });
}

async function loadCourses() {
  try {
    const data = await fetchJson("/api/courses");
    availableCourses = data.courses || [];
    if (availableCourses.length) {
      const selected = availableCourses.find((course) => course.id === currentCourseId) || availableCourses[0];
      currentCourseId = selected.id;
      if (!currentPracticeCourse) currentPracticeCourse = selected.name;
      renderCoursePicker(availableCourses);
    }
  } catch (error) {
    notify(`Courses unavailable: ${error.message}`);
  }
}

async function selectCourse(course) {
  currentCourseId = course.id || currentCourseId;
  updateLearningContext({
    course: course.name,
    topic: course.default_topic || course.defaultTopic || "General review",
    prompt: course.default_topic || course.defaultTopic || "General review",
    documentId: "",
    sessionId: null
  });
  renderCoursePicker(availableCourses);
  clearMessages();
  addMessage(`Course set to ${currentPracticeCourse}. Current focus: ${currentPracticeTopic}.`, "tutor", "Course context");
  await refreshRagStatus();
  await loadChatSessions();
  await refreshDashboard();
  notify(`Course selected: ${currentPracticeCourse}`);
  showView("tutor");
}

function decodeGoogleCredential(credential) {
  const payload = credential?.split(".")?.[1];
  if (!payload) throw new Error("Google credential is missing a profile payload");
  const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
  const json = decodeURIComponent(
    atob(normalized)
      .split("")
      .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
      .join("")
  );
  return JSON.parse(json);
}

function completeGoogleLogin(profile) {
  currentUser = {
    name: profile?.name || profile?.email || "Student",
    email: profile?.email || ""
  };
  document.body.classList.add("is-authenticated");
  const userChip = document.querySelector("#user-chip");
  if (userChip) {
    userChip.textContent = currentUser.name;
    userChip.title = currentUser.email || currentUser.name;
    userChip.classList.remove("hidden");
  }
  notify(`Signed in as ${currentUser.name}`);
  showView("course-start");
  refreshDashboard();
}

function revealDemoLogin(message = "Teacher demo login is available for this cloud session.") {
  const demoButton = document.querySelector("#demo-login");
  const status = document.querySelector("#login-status");
  if (demoButton) demoButton.classList.remove("hidden");
  if (status) status.textContent = message;
}

function completeDemoLogin() {
  completeGoogleLogin({ name: "Teacher demo", email: "cloud-demo@tutorflow.local" });
}

function handleGoogleCredential(response) {
  try {
    const profile = decodeGoogleCredential(response?.credential);
    completeGoogleLogin(profile);
  } catch (error) {
    const status = document.querySelector("#login-status");
    if (status) status.textContent = `Google Sign-In failed: ${error.message}`;
  }
}

function waitForGoogleIdentityScript(timeoutMs = 4000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const check = () => {
      if (window.google?.accounts?.id) {
        resolve();
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        reject(new Error("Google Sign-In script did not finish loading"));
        return;
      }
      setTimeout(check, 120);
    };
    check();
  });
}

async function setupGoogleSignInButton() {
  if (googleSignInReady) return true;
  const status = document.querySelector("#login-status");
  const slot = document.querySelector("#google-button-slot");
  const fallbackButton = document.querySelector("#google-login");

  try {
    const data = await fetchJson("/api/auth/google/login");
    if (data.demoLogin) {
      revealDemoLogin(data.configured
        ? "Use Google Sign-In, or continue with teacher demo for this cloud session."
        : "Google Sign-In is not configured for this public URL. Use teacher demo for Colab.");
    }
    if (!data.configured || !data.clientId) {
      if (status) status.textContent = data.message || "Google Sign-In is not configured yet.";
      if (data.demoLogin) {
        revealDemoLogin("Google Sign-In is not configured for this public URL. Use teacher demo for Colab.");
      }
      return false;
    }

    await waitForGoogleIdentityScript();
    window.google.accounts.id.initialize({
      client_id: data.clientId,
      callback: handleGoogleCredential
    });
    if (slot) {
      slot.innerHTML = "";
      window.google.accounts.id.renderButton(slot, {
        theme: "outline",
        size: "large",
        text: "signin_with",
        shape: "rectangular",
        width: Math.min(420, slot.clientWidth || 420)
      });
    }
    fallbackButton?.classList.add("hidden");
    if (status) status.textContent = "Choose your Google account to continue.";
    googleSignInReady = true;
    return true;
  } catch (error) {
    if (status) status.textContent = `Google Sign-In unavailable: ${error.message}`;
    return false;
  }
}

function inferCourseFromDocument(doc) {
  const title = `${doc?.title || ""}`.toLowerCase();
  if (title.includes("circuit")) return "Electric Circuits";
  if (title.includes("program") || title.includes(" c ")) return "C Programming";
  if (title.includes("calculus")) return "Calculus II";
  return doc?.title || currentPracticeCourse;
}

function inferTopicFromDocument(doc) {
  const title = `${doc?.title || ""}`.toLowerCase();
  if (title.includes("circuit")) return "Ohm's law";
  if (title.includes("program") || title.includes(" c ")) return "Pointers in C";
  if (title.includes("calculus")) return "Integration by parts";
  return currentPracticeTopic;
}

async function refreshModelLabel() {
  try {
    const response = await fetch(`${API_BASE}/api/model/health`);
    if (!response.ok) return;
    const data = await response.json();
    currentModelLabel = data.model || currentModelLabel;
  } catch (error) {
    // Keep the generic label when the backend is not running yet.
  }
}

refreshModelLabel();
function formatDateBadge(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return `NOW<br><small></small>`;
  }
  const day = String(date.getDate()).padStart(2, "0");
  const month = date.toLocaleString("en-US", { month: "short" }).toUpperCase();
  return `${day}<br><small>${month}</small>`;
}

function formatShortDate(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function getValidDate(value, fallback = new Date()) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : new Date(fallback);
}

function startOfWeek(date) {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - start.getDay());
  return start;
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function sameDate(left, right) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function monthTitle(date) {
  return date.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function renderMiniCalendar(baseDate) {
  const grid = document.querySelector("#mini-calendar-grid");
  const label = document.querySelector("#mini-calendar-month");
  if (!grid) return;
  if (label) label.textContent = monthTitle(baseDate);

  const monthStart = new Date(baseDate.getFullYear(), baseDate.getMonth(), 1);
  const firstCell = startOfWeek(monthStart);
  const today = new Date();
  const weekdays = ["S", "M", "T", "W", "T", "F", "S"];
  const cells = weekdays.map((day) => `<span class="mini-weekday">${day}</span>`);
  for (let index = 0; index < 42; index += 1) {
    const date = addDays(firstCell, index);
    const classes = [
      date.getMonth() !== baseDate.getMonth() ? "muted" : "",
      sameDate(date, today) ? "active" : ""
    ].filter(Boolean).join(" ");
    cells.push(`<span class="${classes}">${date.getDate()}</span>`);
  }
  grid.innerHTML = cells.join("");
}

function percentFromScore(score) {
  const numeric = Number(score || 0);
  return Math.round(numeric <= 1 ? numeric * 100 : numeric);
}

function renderProgressAlert(report) {
  const alert = report?.alerts?.[0];
  const titleElement = document.querySelector("#alert-title");
  const messageElement = document.querySelector("#alert-message");
  const actionElement = document.querySelector("#alert-action");
  const notificationElement = document.querySelector("#notification-count");
  if (!titleElement || !messageElement || !actionElement) return;

  if (!alert) {
    titleElement.textContent = "No weak topic detected yet";
    messageElement.textContent = "Complete a practice quiz to let the Progress Monitor Agent detect weak topics.";
    actionElement.textContent = "Start tutoring";
    if (notificationElement) notificationElement.textContent = "0";
    return;
  }

  titleElement.textContent = `${alert.topic} needs attention`;
  messageElement.textContent = alert.message;
  actionElement.textContent = "Review this topic";
  if (notificationElement) notificationElement.textContent = report.summary?.alerts ?? report.alerts.length;
}

function renderCourseProgressFromReport(report) {
  const box = document.querySelector("#course-progress");
  const topics = report?.topics || [];
  if (!box || !topics.length) return false;

  const byCourse = new Map();
  topics.forEach((topic) => {
    const course = topic.course || "Current course";
    const summary = byCourse.get(course) || { course, mastery: [], topics: 0, mastered: 0, weak: [] };
    summary.mastery.push(Number(topic.mastery || 0));
    summary.topics += 1;
    if (Number(topic.mastery || 0) >= 80) summary.mastered += 1;
    if (Number(topic.mastery || 0) < 70) summary.weak.push(topic.topic);
    byCourse.set(course, summary);
  });

  box.innerHTML = [...byCourse.values()].map((course) => {
    const average = Math.round(course.mastery.reduce((sum, score) => sum + score, 0) / course.mastery.length);
    const warning = average < 70;
    const weakLabel = course.weak.length ? `Weak: ${course.weak.slice(0, 2).join(", ")}` : `${course.mastered} of ${course.topics} topics mastered`;
    return `
      <div class="course-row">
        <div><strong>${escapeHtml(course.course)}</strong><small>${escapeHtml(weakLabel)}</small></div>
        <div class="course-score ${warning ? "warning" : ""}">${average}%</div>
        <div class="progress ${warning ? "warning-bar" : ""}"><span style="width:${average}%"></span></div>
      </div>
    `;
  }).join("");
  return true;
}

function renderCourseProgress(attempts) {
  const box = document.querySelector("#course-progress");
  if (!box || !attempts.length) return;

  const byCourse = new Map();
  attempts.forEach((attempt) => {
    const course = attempt.course || "Current course";
    const summary = byCourse.get(course) || { course, scores: [], topics: new Set(), mastered: new Set() };
    const topic = attempt.topic || "Current topic";
    const score = percentFromScore(attempt.score);
    summary.scores.push(score);
    summary.topics.add(topic);
    if (score >= 80) summary.mastered.add(topic);
    byCourse.set(course, summary);
  });

  box.innerHTML = [...byCourse.values()].map((course) => {
    const average = Math.round(course.scores.reduce((sum, score) => sum + score, 0) / course.scores.length);
    const warning = average < 70;
    return `
      <div class="course-row">
        <div><strong>${escapeHtml(course.course)}</strong><small>${course.mastered.size} of ${course.topics.size} topics mastered</small></div>
        <div class="course-score ${warning ? "warning" : ""}">${average}%</div>
        <div class="progress ${warning ? "warning-bar" : ""}"><span style="width:${average}%"></span></div>
      </div>
    `;
  }).join("");
}

function renderActivityList(attempts, sessions, documents, deadlines = []) {
  const box = document.querySelector("#activity-list");
  if (!box) return;

  const items = [
    ...deadlines.slice(0, 3).map((deadline) => ({
      date: deadline.dueDate,
      title: deadline.title,
      detail: `${deadline.course} - ${deadline.type} from ${deadline.source}`
    })),
    ...attempts.slice(0, 3).map((attempt) => ({
      date: attempt.created_at,
      title: `${attempt.correct ? "Passed" : "Reviewed"} ${attempt.topic}`,
      detail: `${attempt.course} - quiz score ${percentFromScore(attempt.score)}%`
    })),
    ...sessions.slice(0, 2).map((session) => ({
      date: session.updated_at,
      title: session.topic,
      detail: `${session.course} - recent tutor chat`
    })),
    ...documents.slice(0, 2).map((document) => ({
      date: null,
      title: document.title,
      detail: `${document.chunks || 0} chunks indexed`
    }))
  ]
    .filter((item) => item.title)
    .sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0))
    .slice(0, 4);

  if (!items.length) return;

  box.innerHTML = items.map((item) => `
    <div class="task">
      <span class="date">${formatDateBadge(item.date)}</span>
      <div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></div>
    </div>
  `).join("");
}

function renderHubOverview({ progressData, attempts, sessions, documents, rag, deadlines }) {
  const summary = progressData?.summary;
  if (summary) {
    setText("#hub-average", `${summary.averageMastery}%`);
    setText("#hub-average-note", `${summary.weakTopics} weak topics`);
    setText("#hub-topics", `${summary.topicsMastered} / ${summary.topicsTracked}`);
    setText("#hub-topics-note", "Topics mastered");
  } else if (attempts.length) {
    const scores = attempts.map((attempt) => percentFromScore(attempt.score));
    const average = Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length);
    setText("#hub-average", `${average}%`);
    setText("#hub-average-note", `${attempts.length} graded attempts`);
  }

  setText("#hub-chats", sessions.length);
  setText("#hub-chats-note", sessions.length === 1 ? "Tutor session" : "Tutor sessions");
  setText("#hub-chunks", rag.indexedChunks || 0);
  setText("#hub-chunks-note", documents.length ? `${documents.length} materials` : "Knowledge chunks");

  const alert = progressData?.alerts?.[0];
  setText("#hub-alert-title", alert ? `${alert.topic} needs attention` : "No weak topic detected yet");
  setText(
    "#hub-alert-message",
    alert ? alert.message : "Complete a practice quiz to let the Progress Monitor suggest what to study next."
  );

  const box = document.querySelector("#hub-activity-list");
  if (!box) return;
  const items = [
    ...(deadlines || []).slice(0, 2).map((deadline) => ({
      date: deadline.dueDate,
      title: deadline.title,
      detail: `${deadline.course} - ${deadline.type}`
    })),
    ...attempts.slice(0, 2).map((attempt) => ({
      date: attempt.created_at,
      title: `${attempt.correct ? "Passed" : "Reviewed"} ${attempt.topic}`,
      detail: `${attempt.course} - ${percentFromScore(attempt.score)}%`
    })),
    ...sessions.slice(0, 1).map((session) => ({
      date: session.updated_at,
      title: session.topic,
      detail: `${session.course} - recent tutor chat`
    }))
  ].filter((item) => item.title).slice(0, 4);

  box.innerHTML = items.length ? items.map((item) => `
    <div class="task">
      <span class="date">${formatDateBadge(item.date)}</span>
      <div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></div>
    </div>
  `).join("") : `<div class="task"><span class="date">NOW<br><small></small></span><div><strong>Pick a course</strong><small>AI Tutor will start with the selected context.</small></div></div>`;
}

function renderStudyPlan(plan) {
  const grid = document.querySelector("#study-plan-grid");
  const summary = document.querySelector("#study-plan-summary");
  const rationale = document.querySelector("#study-plan-rationale");
  if (!grid || !plan?.items?.length) return;

  if (summary) {
    summary.textContent = `Generated from weak topics, ${plan.deadlines?.length || 0} calendar/deadline items, and ${Math.round((plan.availableMinutes || 0) / 60)} available study hours.`;
  }
  if (rationale) {
    rationale.textContent = plan.rationale || "The plan prioritizes weak topics first, then upcoming deadlines.";
  }

  const firstItemDate = plan.items.find((item) => item.date)?.date;
  const anchorDate = getValidDate(firstItemDate, new Date());
  const weekStart = startOfWeek(anchorDate);
  const weekDates = Array.from({ length: 7 }, (_, index) => addDays(weekStart, index));
  const weekDays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  const hours = Array.from({ length: 14 }, (_, index) => index + 7);
  const today = new Date();
  const dayBuckets = weekDates.map(() => []);

  plan.items.slice(0, 14).forEach((item, index) => {
    const itemDate = item.date ? getValidDate(item.date, addDays(weekStart, index % 7)) : null;
    let dayIndex = itemDate ? weekDates.findIndex((date) => sameDate(date, itemDate)) : -1;
    if (dayIndex < 0 && item.day) dayIndex = weekDays.indexOf(String(item.day).slice(0, 3).toUpperCase());
    if (dayIndex < 0) dayIndex = index % 7;
    const slot = 8 + (dayBuckets[dayIndex].length * 1.35);
    dayBuckets[dayIndex].push({ ...item, slot });
  });

  const rangeTitle = document.querySelector("#calendar-range-title");
  if (rangeTitle) rangeTitle.textContent = monthTitle(weekStart);
  renderMiniCalendar(anchorDate);

  const head = `
    <div class="calendar-week-head">
      <div class="timezone-cell">GMT+7</div>
      ${weekDates.map((date, index) => `
        <div class="calendar-day-head ${sameDate(date, today) ? "today" : ""}">
          <div><small>${weekDays[index]}</small><strong>${date.getDate()}</strong></div>
        </div>
      `).join("")}
    </div>
  `;

  const labels = `<div class="calendar-time-labels">${hours.map((hour) => {
    const display = hour === 12 ? "12 PM" : hour > 12 ? `${hour - 12} PM` : `${hour} AM`;
    return `<div class="calendar-hour-label">${display}</div>`;
  }).join("")}</div>`;

  const columns = dayBuckets.map((items) => `
    <div class="calendar-day-column">
      ${items.map((item) => {
        const priority = item.priority === "High";
        const review = item.activity && String(item.activity).toLowerCase().includes("review");
        const deadline = item.deadline ? `${item.deadline.title} due ${item.deadline.dueDate}` : item.reason;
        const minutes = Math.max(25, Number(item.minutes || 35));
        const top = Math.max(0, (Number(item.slot || 8) - 7) * 60);
        const height = Math.min(110, Math.max(46, minutes * 1.25));
        return `
          <div class="calendar-event ${priority ? "priority" : review ? "review" : ""}" style="top:${top}px;height:${height}px">
            <span>${minutes} min</span>
            <strong>${escapeHtml(item.topic)}</strong>
            <small>${escapeHtml(item.course)}</small>
            <small>${escapeHtml(item.activity)}${deadline ? ` - ${escapeHtml(deadline)}` : ""}</small>
          </div>
        `;
      }).join("")}
    </div>
  `).join("");

  grid.innerHTML = `${head}<div class="calendar-time-grid">${labels}${columns}</div>`;
}

function renderCalendarEvents(events = []) {
  const box = document.querySelector("#calendar-events");
  if (!box) return;

  const personalEvents = events.filter((event) => event.source === "Personal calendar");
  const shownEvents = (personalEvents.length ? personalEvents : events).slice(0, 6);
  if (!shownEvents.length) {
    box.innerHTML = `<div class="source-card"><strong>No personal calendar yet</strong><small>Add an exam or assignment to personalize the plan.</small></div>`;
    return;
  }

  box.innerHTML = shownEvents.map((event) => `
    <div class="source-card">
      <strong>${escapeHtml(event.title)}</strong>
      <small>${escapeHtml(event.course)} - ${escapeHtml(event.topic)} - ${escapeHtml(event.type)} due ${escapeHtml(event.dueDate)}</small>
      <small>${escapeHtml(event.source)}</small>
    </div>
  `).join("");
}

async function refreshDashboard() {
  const [documentsResult, sessionsResult, attemptsResult, progressResult, planResult] = await Promise.allSettled([
    fetchJson(`/api/documents${courseQuery()}`),
    fetchJson(`/api/chat/sessions${courseQuery()}`),
    fetchJson(`/api/practice/attempts${courseQuery()}`),
    fetchJson(`/api/progress/monitor${courseQuery()}`),
    fetchJson(`/api/study-plan${courseQuery()}`)
  ]);

  const documentsData = documentsResult.status === "fulfilled" ? documentsResult.value : {};
  const sessionsData = sessionsResult.status === "fulfilled" ? sessionsResult.value : {};
  const attemptsData = attemptsResult.status === "fulfilled" ? attemptsResult.value : {};
  const progressData = progressResult.status === "fulfilled" ? progressResult.value : null;
  const planData = planResult.status === "fulfilled" ? planResult.value : null;
  const documents = documentsData.documents || [];
  const sessions = sessionsData.sessions || [];
  const attempts = attemptsData.attempts || [];
  const rag = documentsData.rag || {};
  latestProgressReport = progressData;
  latestStudyPlan = planData;

  setText("#stat-chats", sessions.length);
  setText("#stat-chats-note", sessions.length === 1 ? "Tutor session" : "Tutor sessions");
  setText("#stat-chunks", rag.indexedChunks || 0);
  setText("#stat-chunks-note", documents.length ? `${documents.length} materials indexed` : "Course materials");
  renderProgressAlert(progressData);
  if (planData) renderStudyPlan(planData);
  renderCalendarEvents(planData?.deadlines || progressData?.deadlines || []);
  renderHubOverview({
    progressData,
    attempts,
    sessions,
    documents,
    rag,
    deadlines: progressData?.deadlines || planData?.deadlines || []
  });

  if (progressData?.summary) {
    setText("#stat-average", `${progressData.summary.averageMastery}%`);
    setText("#stat-average-note", `${progressData.summary.weakTopics} weak topics`);
    setText("#stat-topics", `${progressData.summary.topicsMastered} / ${progressData.summary.topicsTracked}`);
    setText("#stat-topics-note", `${Math.max(0, progressData.summary.topicsTracked - progressData.summary.topicsMastered)} in progress`);
    renderCourseProgressFromReport(progressData);
  }

  if (!progressData?.summary && attempts.length) {
    const scores = attempts.map((attempt) => percentFromScore(attempt.score));
    const average = Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length);
    const topics = new Set(attempts.map((attempt) => `${attempt.course}:${attempt.topic}`));
    const mastered = new Set(
      attempts
        .filter((attempt) => percentFromScore(attempt.score) >= 80)
        .map((attempt) => `${attempt.course}:${attempt.topic}`)
    );

    setText("#stat-average", `${average}%`);
    setText("#stat-average-note", `${attempts.length} graded attempts`);
    setText("#stat-topics", `${mastered.size} / ${topics.size}`);
    setText("#stat-topics-note", `${Math.max(0, topics.size - mastered.size)} in progress`);
    renderCourseProgress(attempts);
  }

  renderActivityList(attempts, sessions, documents, progressData?.deadlines || planData?.deadlines || []);
}
function formatDocument(doc) {
  const pages = doc.pageCount ? `${doc.pageCount} pages` : "No pages";
  const embedded = `${doc.embeddedChunks || 0}/${doc.chunks || 0} embedded`;
  return `
    <div class="source-card document-card">
      <strong>${escapeHtml(doc.title)}</strong>
      <small>${pages} - ${embedded}</small>
      <div class="document-actions">
        <button class="practice-document" data-document-id="${escapeHtml(doc.documentId)}">Practice</button>
        <button class="delete-document" data-document-id="${escapeHtml(doc.documentId)}" data-title="${escapeHtml(doc.title)}">Delete</button>
      </div>
    </div>
  `;
}

async function deleteDocument(documentId, title) {
  if (!confirm(`Delete "${title}" from the knowledge base?`)) return;

  try {
    const response = await fetch(`${API_BASE}/api/documents/${encodeURIComponent(documentId)}`, {
      method: "DELETE"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

    await refreshRagStatus();
    await refreshDashboard();
    notify("Document deleted");
  } catch (error) {
    notify(`Delete failed: ${error.message}`);
  }
}

function populatePracticeDocuments(documents) {
  availableDocuments = documents || [];
  const select = document.querySelector("#practice-document");
  if (!select) return;

  const selected = currentPracticeDocumentId;
  select.innerHTML = `
    <option value="">All uploaded materials</option>
    ${availableDocuments.map((doc) => `
      <option value="${escapeHtml(doc.documentId)}">${escapeHtml(doc.title)}</option>
    `).join("")}
  `;
  if (selected && availableDocuments.some((doc) => doc.documentId === selected)) {
    select.value = selected;
  } else if (selected) {
    currentPracticeDocumentId = "";
  }
}

function startPracticeFromDocument(documentId) {
  const doc = availableDocuments.find((item) => item.documentId === documentId);
  if (!doc) return;
  updateLearningContext({
    course: currentPracticeCourse,
    topic: inferTopicFromDocument(doc),
    prompt: inferTopicFromDocument(doc),
    documentId,
    sessionId: null
  });
  renderPracticeIdle();
  showView("assessment");
  notify("Select or edit the topic, then generate practice");
}

async function refreshRagStatus() {
  const statusCard = document.querySelector("#rag-status small");
  const documentsBox = document.querySelector("#uploaded-documents");
  if (!statusCard || !documentsBox) return;

  try {
    const response = await fetch(`${API_BASE}/api/documents${courseQuery()}`);
    if (!response.ok) throw new Error(`Backend returned ${response.status}`);
    const data = await response.json();
    const rag = data.rag || {};
    populatePracticeDocuments(data.documents || []);
    statusCard.textContent = `${rag.indexedChunks || 0} chunks - ${rag.embeddedChunks || 0} embedded - ${rag.retrievalMode || "unknown"}`;
    documentsBox.innerHTML = data.documents?.length
      ? data.documents.map(formatDocument).join("")
      : `<div class="source-card"><strong>No documents indexed</strong><small>Upload a textbook, lecture note, or markdown file.</small></div>`;
    documentsBox.querySelectorAll(".delete-document").forEach((button) => {
      button.addEventListener("click", () => deleteDocument(button.dataset.documentId, button.dataset.title));
    });
    documentsBox.querySelectorAll(".practice-document").forEach((button) => {
      button.addEventListener("click", () => startPracticeFromDocument(button.dataset.documentId));
    });
  } catch (error) {
    statusCard.textContent = "Backend unavailable";
  }
}

async function bootstrapWorkspace() {
  await loadCourses();
  await refreshRagStatus();
  await loadChatSessions();
  await refreshDashboard();
  const params = new URLSearchParams(window.location.search);
  if (params.get("googleCalendar") === "connected") {
    notify("Google Calendar connected");
    const status = document.querySelector("#google-calendar-status");
    if (status) status.textContent = "Google Calendar connected. Click Sync Google Calendar to import events.";
  } else if (params.get("googleCalendar") === "error") {
    notify("Google Calendar connection failed");
  }
}

bootstrapWorkspace();

function updateLearningContext({ course, topic, prompt, sessionId, documentId } = {}) {
  if (course) {
    currentPracticeCourse = course.trim() || currentPracticeCourse;
    const matchedCourse = courseByName(currentPracticeCourse);
    if (matchedCourse) currentCourseId = matchedCourse.id;
  }
  if (topic) currentPracticeTopic = topic.trim() || currentPracticeTopic;
  if (prompt) currentPracticePrompt = prompt.trim() || currentPracticePrompt;
  if (documentId !== undefined) currentPracticeDocumentId = documentId || "";
  if (sessionId !== undefined) currentSessionId = sessionId;

  setText("#current-course-label", currentPracticeCourse);
  setText("#current-topic-label", currentPracticeTopic);
  setText("#chat-context-label", `${currentPracticeCourse} - ${currentPracticeTopic}`);
  setText("#practice-context", `${currentPracticeCourse} - ${currentPracticeTopic}`);
  const courseInput = document.querySelector("#course-input");
  const topicInput = document.querySelector("#topic-input");
  if (courseInput) courseInput.value = currentPracticeCourse;
  if (topicInput) topicInput.value = currentPracticeTopic;
  const practiceCourse = document.querySelector("#practice-course");
  const practiceTopic = document.querySelector("#practice-topic");
  const practiceDocument = document.querySelector("#practice-document");
  const calendarCourse = document.querySelector("#calendar-course");
  const calendarTopic = document.querySelector("#calendar-topic");
  if (practiceCourse) practiceCourse.value = currentPracticeCourse;
  if (practiceTopic) practiceTopic.value = currentPracticeTopic;
  if (practiceDocument) practiceDocument.value = currentPracticeDocumentId;
  if (calendarCourse) calendarCourse.value = currentPracticeCourse;
  if (calendarTopic) calendarTopic.value = currentPracticeTopic;
}

function guessTopic(text) {
  const lower = text.toLowerCase();
  if (lower.includes("pointer")) return "Pointers in C";
  if (lower.includes("array")) return "Arrays in C";
  if (lower.includes("string")) return "Strings in C";
  if (lower.includes("structure") || lower.includes("struct")) return "Structures in C";
  if (lower.includes("function")) return "Functions in C";
  if (lower.includes("ohm")) return "Ohm's law";
  if (lower.includes("kirchhoff voltage") || lower.includes("kvl")) return "Kirchhoff's voltage law";
  if (lower.includes("kirchhoff current") || lower.includes("kcl")) return "Kirchhoff's current law";
  if (lower.includes("node voltage") || lower.includes("nodal")) return "Node-voltage method";
  if (lower.includes("mesh")) return "Mesh-current method";
  if (lower.includes("integration") || lower.includes("integral")) return "Integration by parts";
  return currentPracticeTopic;
}

function guessCourse(topic) {
  const lower = topic.toLowerCase();
  if (lower.includes(" in c")) {
    return "C Programming";
  }
  if (lower.includes("ohm") || lower.includes("kirchhoff") || lower.includes("node") || lower.includes("mesh")) {
    return "Electric Circuits";
  }
  return "Calculus II";
}

const titles = {
  login: "Sign in to TutorFlow",
  "course-start": "Course hub",
  dashboard: "Good afternoon, Thuan",
  tutor: "Learn with your AI tutor",
  knowledge: "Manage course knowledge",
  assessment: "Practice at your level",
  plan: "Your study plan"
};

updateLearningContext();

function showView(id) {
  views.forEach((view) => view.classList.toggle("active", view.id === id));
  navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === id));
  title.textContent = titles[id] || titles.dashboard;
}

showView("login");
navItems.forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
setupGoogleSignInButton();
document.querySelector("#google-login")?.addEventListener("click", async () => {
  const status = document.querySelector("#login-status");
  if (status) status.textContent = "Loading Google Sign-In button...";
  const ready = await setupGoogleSignInButton();
  if (!ready) notify("Google Sign-In config required");
});
document.querySelector("#demo-login")?.addEventListener("click", completeDemoLogin);
document.querySelector("#course-create-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.querySelector("#course-create-status");
  const nameInput = document.querySelector("#new-course-name");
  const topicInput = document.querySelector("#new-course-topic");
  const payload = {
    name: nameInput?.value.trim() || "",
    description: topicInput?.value.trim() ? `Focus: ${topicInput.value.trim()}` : "Custom course workspace",
    defaultTopic: topicInput?.value.trim() || "General review"
  };
  if (!payload.name) return;
  if (status) status.textContent = "Creating course...";
  try {
    const response = await fetch(`${API_BASE}/api/courses`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);
    await loadCourses();
    event.target.reset();
    if (status) status.textContent = "Course created. It now has separate materials, chats, quizzes, and plan.";
    await selectCourse(data.course);
  } catch (error) {
    if (status) status.textContent = `Course create failed: ${error.message}`;
  }
});
document.querySelectorAll(".course-card-button").forEach((button) => {
  button.addEventListener("click", () => {
    selectCourse({
      id: button.dataset.courseId,
      name: button.dataset.course,
      default_topic: button.dataset.topic
    });
  });
});
document.querySelector("[data-open-tutor]").addEventListener("click", () => {
  const alert = latestProgressReport?.alerts?.[0];
  if (alert) {
    updateLearningContext({
      course: alert.course,
      topic: alert.topic,
      prompt: alert.topic,
      sessionId: null
    });
  }
  showView("tutor");
});
document.querySelector("[data-view-button]").addEventListener("click", () => showView("plan"));
document.querySelector("#quiz-from-chat").addEventListener("click", async () => { showView("assessment"); await loadPracticeQuestions(); });
document.querySelector("#go-upload")?.addEventListener("click", () => showView("knowledge"));
document.querySelector("#go-practice")?.addEventListener("click", async () => {
  showView("assessment");
  renderPracticeIdle();
});
document.querySelector("#go-study-plan")?.addEventListener("click", () => showView("plan"));
document.querySelector("#focus-calendar-title")?.addEventListener("click", () => {
  showView("plan");
  document.querySelector("#calendar-title")?.focus();
});
document.querySelector("#generate-practice").addEventListener("click", loadPracticeQuestions);
document.querySelector("#practice-document").addEventListener("change", (event) => {
  const documentId = event.target.value;
  const doc = availableDocuments.find((item) => item.documentId === documentId);
  updateLearningContext({
    course: currentPracticeCourse,
    topic: doc ? inferTopicFromDocument(doc) : currentPracticeTopic,
    prompt: doc ? inferTopicFromDocument(doc) : currentPracticePrompt,
    documentId
  });
  renderPracticeIdle();
});
document.querySelector("#practice-course").addEventListener("input", (event) => {
  updateLearningContext({ course: event.target.value });
});
document.querySelector("#practice-topic").addEventListener("input", (event) => {
  updateLearningContext({ topic: event.target.value, prompt: event.target.value });
});
const calendarForm = document.querySelector("#calendar-event-form");
if (calendarForm) {
  calendarForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.querySelector("#calendar-status");
    const payload = {
      courseId: currentCourseId,
      course: document.querySelector("#calendar-course")?.value || currentPracticeCourse,
      topic: document.querySelector("#calendar-topic")?.value || currentPracticeTopic,
      title: document.querySelector("#calendar-title")?.value || "Study event",
      type: document.querySelector("#calendar-type")?.value || "assignment",
      dueDate: document.querySelector("#calendar-date")?.value || "",
      source: "Personal calendar"
    };

    if (!payload.dueDate) {
      notify("Choose a due date first");
      return;
    }

    if (status) status.textContent = "Saving calendar event...";
    try {
      const response = await fetch(`${API_BASE}/api/calendar/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

      if (status) status.textContent = "Calendar saved. Alerts and study plan updated.";
      document.querySelector("#calendar-title").value = "";
      await refreshDashboard();
      notify("Personal calendar updated");
    } catch (error) {
      if (status) status.textContent = `Calendar save failed: ${error.message}`;
      notify("Could not save calendar event");
    }
  });
}
document.querySelector("#connect-google-calendar")?.addEventListener("click", async () => {
  const status = document.querySelector("#google-calendar-status");
  try {
    const info = await fetchJson("/api/google/calendar/status");
    const auth = await fetchJson("/api/auth/google/login");
    if (info.connected) {
      if (status) status.textContent = "Google Calendar is already connected. Use Sync Google Calendar.";
      return;
    }
    if (info.calendarConfigured && auth.configured && auth.authUrl) {
      window.location.href = auth.authUrl;
      return;
    }
    if (status) {
      const originHint = info.javascriptOrigin ? ` Origin: ${info.javascriptOrigin}.` : "";
      const redirectHint = info.redirectUri ? ` Redirect URI: ${info.redirectUri}.` : "";
      status.textContent = `${info.message || auth.message || "Google Calendar OAuth credentials are not configured."}${originHint}${redirectHint}`;
    }
    notify("Google Calendar needs OAuth credentials");
  } catch (error) {
    if (status) status.textContent = `Google Calendar unavailable: ${error.message}`;
    notify("Google Calendar connection failed");
  }
});
document.querySelector("#sync-google-calendar")?.addEventListener("click", async () => {
  const status = document.querySelector("#google-calendar-status");
  if (status) status.textContent = "Syncing Google Calendar events...";
  try {
    const response = await fetch(`${API_BASE}/api/google/calendar/sync?course=${encodeURIComponent(currentPracticeCourse)}`, {
      method: "POST"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);
    if (status) status.textContent = `Imported ${data.imported || 0} Google Calendar events.`;
    await refreshDashboard();
    notify("Google Calendar synced");
  } catch (error) {
    if (status) status.textContent = `Sync failed: ${error.message}`;
    notify("Google Calendar sync failed");
  }
});
document.querySelector("#topic-form").addEventListener("submit", (event) => {
  event.preventDefault();
  updateLearningContext({
    course: document.querySelector("#course-input").value,
    topic: document.querySelector("#topic-input").value,
    prompt: document.querySelector("#topic-input").value,
    sessionId: null
  });
  clearMessages();
  addMessage(`Ready for ${currentPracticeTopic}. Ask a question when you want to continue.`, "tutor", "Current topic");
  loadChatSessions();
  notify("Topic updated");
});
document.querySelector("#new-chat").addEventListener("click", () => {
  updateLearningContext({ sessionId: null, prompt: currentPracticeTopic });
  clearMessages();
  addMessage(`New chat for ${currentPracticeTopic}.`, "tutor", "Current topic");
  loadChatSessions();
});

function notify(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2300);
}

const responses = [
  {
    keywords: ["simple", "explain", "what"],
    text: "Integration by parts reverses the product rule. Choose one factor to differentiate (u) and the other to integrate (dv), then apply integral u dv = uv - integral v du. A useful selection guide is LIATE: logarithmic, inverse trig, algebraic, trig, exponential.",
    source: "[1] Lecture 08, pp. 12-13"
  },
  {
    keywords: ["example", "show", "work"],
    text: "For integral x cos(x) dx, choose u = x and dv = cos(x)dx. Then du = dx and v = sin(x). Substitution gives x sin(x) - integral sin(x)dx = x sin(x) + cos(x) + C.",
    source: "[2] Calculus Textbook, Ch. 7.1"
  },
  {
    keywords: ["hint", "try"],
    text: "Hint first: identify which part becomes simpler when differentiated. In integral x e^x dx, differentiating x gives 1, so x is a strong choice for u. What should dv be?",
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
  const timeoutId = setTimeout(() => controller.abort(), 90000);

  try {
    const response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: input,
        courseId: currentCourseId,
        course: currentPracticeCourse,
        topic: currentPracticeTopic,
        sessionId: currentSessionId
      })
    });

    if (!response.ok) {
      throw new Error(`Backend returned ${response.status}`);
    }

    const data = await response.json();
    currentModelLabel = data.model || currentModelLabel;
    const modelLabel = data.model ? `${data.model} via Ollama` : "Ollama model";
    const sourceItems = data.citations?.length ? data.citations : data.retrievedSources;
    const sourceLabel = sourceItems?.length
      ? `${sourceItems.map((item) => `[${item.sourceId}] ${item.title}, p. ${item.page}`).join("; ")} | ${modelLabel}`
      : modelLabel;

    onToken(data.answer || "No answer returned from model.");
    return {
      text: data.answer || "No answer returned from model.",
      source: sourceLabel,
      model: data.model || "Ollama",
      sessionId: data.sessionId
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

function clearMessages() {
  document.querySelector("#messages").innerHTML = "";
}

function renderSavedMessage(message) {
  const metadata = message.metadata || {};
  const citations = metadata.citations?.length ? metadata.citations : metadata.retrievedSources;
  const source = citations?.length
    ? citations.map((item) => `[${item.sourceId}] ${item.title}, p. ${item.page}`).join("; ")
    : (metadata.model || "Saved message");
  addMessage(message.content, message.role === "user" ? "user" : "tutor", source);
}

async function loadChatSessions() {
  const box = document.querySelector("#chat-sessions");
  if (!box) return;

  try {
    const response = await fetch(`${API_BASE}/api/chat/sessions${courseQuery()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

    box.innerHTML = data.sessions?.length
      ? data.sessions.map((session) => `
          <button class="session-card ${session.id === currentSessionId ? "active" : ""}" data-session-id="${session.id}">
            <strong>${escapeHtml(session.topic)}</strong>
            <small>${escapeHtml(session.course)} - ${escapeHtml(session.title)}</small>
          </button>
        `).join("")
      : `<div class="source-card"><strong>No saved chats yet</strong><small>Ask a question to create a topic session.</small></div>`;

    box.querySelectorAll(".session-card").forEach((button) => {
      button.addEventListener("click", () => loadChatSession(button.dataset.sessionId));
    });
  } catch (error) {
    box.innerHTML = `<div class="source-card"><strong>Chat history unavailable</strong><small>${escapeHtml(error.message)}</small></div>`;
  }
}

async function loadChatSession(sessionId) {
  try {
    const response = await fetch(`${API_BASE}/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

    const firstUser = data.messages.find((message) => message.role === "user");
    const firstMetadata = firstUser?.metadata || {};
    updateLearningContext({
      course: firstMetadata.course || currentPracticeCourse,
      topic: firstMetadata.topic || currentPracticeTopic,
      prompt: firstUser?.content || currentPracticePrompt,
      sessionId
    });

    clearMessages();
    data.messages.forEach(renderSavedMessage);
    await loadChatSessions();
    await refreshDashboard();
    showView("tutor");
  } catch (error) {
    notify(`Could not load chat: ${error.message}`);
  }
}

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#chat-text");
  const text = input.value.trim();
  if (!text) return;
  const detectedTopic = guessTopic(text);
  updateLearningContext({
    topic: detectedTopic,
    prompt: text
  });
  addMessage(text, "user");
  input.value = "";
  addMessage(`Thinking with ${currentModelLabel}...`, "tutor", "Backend / Ollama");

  const messages = document.querySelector("#messages");
  const pendingMessage = messages.lastElementChild;

  try {
    const reply = await askBackendTutor(text, (partial) => { pendingMessage.querySelector("p").textContent = partial || `Thinking with ${currentModelLabel}...`; });
    currentSessionId = reply.sessionId || currentSessionId;
    pendingMessage.querySelector("p").textContent = reply.text;
    pendingMessage.querySelector(".citation").textContent = reply.source;
    await loadChatSessions();
    await refreshDashboard();
  } catch (error) {
    const fallback = tutorReply(text);
    pendingMessage.querySelector("p").textContent = `${fallback.text} (Fallback because backend is not available yet.)`;
    pendingMessage.querySelector(".citation").textContent = fallback.source;
    notify("Backend unavailable - using fallback tutor response");
  }
});


const uploadForm = document.querySelector("#document-upload-form");
if (uploadForm) {
  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fileInput = document.querySelector("#document-file");
    const titleInput = document.querySelector("#document-title");
    const keywordsInput = document.querySelector("#document-keywords");
    const uploadStatus = document.querySelector("#upload-status");

    if (!fileInput.files.length) return;
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("title", titleInput.value.trim() || fileInput.files[0].name);
    formData.append("keywords", keywordsInput.value.trim());
    formData.append("course", currentPracticeCourse);
    formData.append("courseId", currentCourseId);
    const requestedTopic = keywordsInput.value.split(",").map((item) => item.trim()).find(Boolean);

    uploadStatus.textContent = "Uploading, chunking, and embedding document...";
    try {
      const response = await fetch(`${API_BASE}/api/documents/upload`, {
        method: "POST",
        body: formData
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

      const doc = data.document;
      const warning = doc.embeddingError ? " Embedding failed, using lexical fallback." : "";
      uploadStatus.textContent = `Indexed ${doc.chunksWritten} chunks, embedded ${doc.embeddedChunks}.${warning}`;
      uploadForm.reset();
      await refreshRagStatus();
      updateLearningContext({
        course: currentPracticeCourse,
        topic: requestedTopic || inferTopicFromDocument(doc),
        prompt: requestedTopic || inferTopicFromDocument(doc),
        documentId: doc.documentId,
        sessionId: null
      });
      await refreshDashboard();

      notify("Document indexed. Topic is ready for practice");
    } catch (error) {
      uploadStatus.textContent = `Upload failed: ${error.message}`;
      notify("Document upload failed");
    }
  });
}
document.querySelectorAll(".quick-prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelector("#chat-text").value = button.textContent;
    document.querySelector("#chat-form").requestSubmit();
  });
});

let practiceQuestions = [];
let questionIndex = 0;
let currentQuestion = null;

function renderAnswerPanel(result) {
  const panel = document.querySelector("#answer-panel");
  if (!panel || !currentQuestion) return;

  const sourceLabel = currentQuestion.sourceIds?.length
    ? `<small>Sources: ${currentQuestion.sourceIds.map(escapeHtml).join(", ")}</small>`
    : "";
  const correctAnswer = result.correctAnswer || currentQuestion.correctAnswer || "See explanation";
  const label = currentQuestion.type === "multiple_choice" ? "Correct answer" : "Reference answer";
  panel.innerHTML = `
    <strong>${label}</strong>
    <div>${escapeHtml(correctAnswer)}</div>
    ${sourceLabel}
  `;
  panel.classList.remove("hidden");
}

function renderPracticeIdle() {
  practiceQuestions = [];
  questionIndex = 0;
  currentQuestion = null;
  setText("#difficulty", "Ready");
  setText("#question-number", "0");
  setText("#question-total", "0");
  setText("#quiz-question", "Select practice settings, then generate questions for the current topic.");
  document.querySelector("#answers").innerHTML = "";
  document.querySelector("#answer-panel").classList.add("hidden");
  document.querySelector("#feedback").classList.add("hidden");
  document.querySelector("#next-question").classList.add("hidden");
}

function loadFallbackPracticeQuestion(reason) {
  practiceQuestions = [{
    id: `client-fallback-${Date.now()}`,
    type: "short_answer",
    level: "Fallback - Review",
    topic: currentPracticeTopic,
    question: `No generated quiz was returned. Write a short explanation of what you know about "${currentPracticeTopic}", then submit it for source-based feedback.`,
    choices: [],
    correctAnswer: "A good answer should match the uploaded course material for this topic.",
    expectedKeywords: currentPracticeTopic.toLowerCase().split(/\s+/).filter((word) => word.length > 2).slice(0, 5),
    explanation: `Fallback question shown because practice generation failed: ${reason}`,
    sourceIds: []
  }];
  questionIndex = 0;
  renderQuestion();
  const feedback = document.querySelector("#feedback");
  feedback.textContent = `Practice generator returned an error, so a fallback review question is shown. ${reason}`;
  feedback.classList.remove("hidden");
}

async function loadPracticeQuestions() {
  const feedback = document.querySelector("#feedback");
  const count = Number(document.querySelector("#quiz-count")?.value || 4);
  const difficulty = document.querySelector("#quiz-difficulty")?.value || "auto";
  const questionType = document.querySelector("#quiz-type")?.value || "mixed";
  updateLearningContext({
    course: document.querySelector("#practice-course")?.value || currentPracticeCourse,
    topic: document.querySelector("#practice-topic")?.value || currentPracticeTopic,
    prompt: document.querySelector("#practice-topic")?.value || currentPracticePrompt,
    documentId: document.querySelector("#practice-document")?.value || ""
  });

  feedback.textContent = "Generating adaptive practice from your current topic...";
  feedback.classList.remove("hidden");
  document.querySelector("#next-question").classList.add("hidden");
  document.querySelector("#answer-panel").classList.add("hidden");
  document.querySelector("#answers").innerHTML = "";

  try {
    const response = await fetch(`${API_BASE}/api/practice/recommend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        courseId: currentCourseId,
        course: currentPracticeCourse,
        topic: currentPracticeTopic,
        prompt: currentPracticePrompt,
        documentId: currentPracticeDocumentId || null,
        mastery: currentMastery,
        count,
        difficulty,
        questionType
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Backend returned ${response.status}`);

    practiceQuestions = data.questions || [];
    questionIndex = 0;
    if (!practiceQuestions.length) throw new Error("No practice questions returned");
    renderQuestion();
    notify(`Practice loaded for ${data.topic}`);
  } catch (error) {
    loadFallbackPracticeQuestion(error.message);
  }
}

function renderQuestion() {
  currentQuestion = practiceQuestions[questionIndex];
  if (!currentQuestion) return;

  document.querySelector("#difficulty").textContent = `${currentQuestion.level.replace("\u00b7", "-")} - ${currentQuestion.type.replace("_", " ")}`;
  document.querySelector("#question-number").textContent = questionIndex + 1;
  document.querySelector("#question-total").textContent = practiceQuestions.length;
  document.querySelector("#quiz-question").textContent = currentQuestion.question;

  const answers = document.querySelector("#answers");
  answers.innerHTML = "";

  if (currentQuestion.type === "multiple_choice") {
    currentQuestion.choices.forEach((answer) => {
      const button = document.createElement("button");
      button.textContent = answer;
      button.addEventListener("click", () => submitPracticeAnswer(answer, button));
      answers.appendChild(button);
    });
  } else {
    const textarea = document.createElement("textarea");
    textarea.className = "short-answer";
    textarea.placeholder = "Write your answer in your own words...";
    textarea.rows = 4;

    const button = document.createElement("button");
    button.className = "primary";
    button.textContent = "Submit answer";
    button.addEventListener("click", () => submitPracticeAnswer(textarea.value, button));

    answers.appendChild(textarea);
    answers.appendChild(button);
  }

  document.querySelector("#feedback").classList.add("hidden");
  document.querySelector("#next-question").classList.add("hidden");
  document.querySelector("#answer-panel").classList.add("hidden");
}

async function submitPracticeAnswer(answer, selectedButton) {
  answer = String(answer || "").trim();
  if (!answer) {
    notify("Please enter an answer first");
    return;
  }

  const allButtons = [...document.querySelectorAll("#answers button")];
  allButtons.forEach((item) => item.disabled = true);

  try {
    const response = await fetch(`${API_BASE}/api/practice/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: currentQuestion,
        answer,
        courseId: currentCourseId,
        course: currentPracticeCourse,
        documentId: currentPracticeDocumentId || null,
        currentMastery
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `Backend returned ${response.status}`);

    currentMastery = result.newMastery;
    document.querySelector("#confidence").textContent = `${currentMastery}%`;
    document.querySelector("#confidence-bar").style.width = `${currentMastery}%`;
    await refreshDashboard();

    if (currentQuestion.type === "multiple_choice" && selectedButton) {
      selectedButton.classList.add(result.correct ? "selected-correct" : "selected-wrong");
      allButtons.forEach((button) => {
        if (button.textContent === currentQuestion.correctAnswer) button.classList.add("selected-correct");
      });
    }

    const feedback = document.querySelector("#feedback");
    renderAnswerPanel(result);
    const correctAnswer = result.correctAnswer ? `<p><strong>Correct answer:</strong> ${escapeHtml(result.correctAnswer)}</p>` : "";
    const missingConcepts = result.missingConcepts?.length
      ? `<p><strong>Missing concepts:</strong> ${result.missingConcepts.map(escapeHtml).join(", ")}</p>`
      : "";
    const hint = result.hint ? `<p><strong>Hint:</strong> ${escapeHtml(result.hint)}</p>` : "";
    feedback.innerHTML = `
      <p><strong>${result.correct ? "Correct" : "Needs review"} - Score ${Math.round((result.score || 0) * 100)}%</strong></p>
      <p>${escapeHtml(result.feedback)}</p>
      ${correctAnswer}
      ${missingConcepts}
      <p><strong>Explanation:</strong> ${escapeHtml(result.explanation)}</p>
      ${hint}
    `;
    feedback.classList.remove("hidden");
    document.querySelector("#next-question").classList.remove("hidden");
  } catch (error) {
    const feedback = document.querySelector("#feedback");
    feedback.textContent = `Could not grade answer: ${error.message}`;
    feedback.classList.remove("hidden");
    allButtons.forEach((item) => item.disabled = false);
  }
}

document.querySelector("#next-question").addEventListener("click", () => {
  if (questionIndex < practiceQuestions.length - 1) {
    questionIndex += 1;
    renderQuestion();
  } else {
    notify("Practice complete - Topic confidence updated");
    showView("dashboard");
    refreshDashboard();
  }
});

document.querySelector("#reset-demo").addEventListener("click", () => {
  questionIndex = 0;
  currentMastery = 46;
  practiceQuestions = [];
  document.querySelector("#confidence").textContent = "46%";
  document.querySelector("#confidence-bar").style.width = "46%";
  notify("Project state reset");
  showView("dashboard");
  refreshDashboard();
});

renderPracticeIdle();



