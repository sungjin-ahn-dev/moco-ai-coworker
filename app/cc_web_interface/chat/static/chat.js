(() => {
  const $ = (id) => document.getElementById(id);
  const convList = $("conv-list");
  const messagesEl = $("messages");
  const emptyState = $("empty-state");
  const input = $("input");
  const sendBtn = $("send-btn");
  const titleEl = $("conv-title");
  const newChatBtn = $("new-chat-btn");
  const sidebarToggle = $("sidebar-toggle");
  const sidebar = $("sidebar");
  const userAvatar = $("user-avatar");
  const scrollBtn = $("scroll-bottom-btn");
  const attachBtn = $("attach-btn");
  const fileInput = $("file-input");
  const pendingFilesEl = $("pending-files");

  const MAX_FILES = 10;
  const MAX_FILE_SIZE = 50 * 1024 * 1024;

  let currentConvId = null;
  let isStreaming = false;
  /** @type {File[]} */
  let pendingFiles = [];

  function formatSize(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  }

  function iconForMime(mime, name) {
    const m = (mime || "").toLowerCase();
    const n = (name || "").toLowerCase();
    if (m.startsWith("image/")) return "🖼️";
    if (m === "application/pdf" || n.endsWith(".pdf")) return "📕";
    if (m.includes("spreadsheet") || n.endsWith(".xlsx") || n.endsWith(".xls") || n.endsWith(".csv")) return "📊";
    if (m.includes("presentation") || n.endsWith(".pptx") || n.endsWith(".ppt")) return "📽️";
    if (m.includes("word") || n.endsWith(".docx") || n.endsWith(".doc")) return "📝";
    if (m.startsWith("audio/")) return "🎵";
    if (m.startsWith("video/")) return "🎬";
    if (m.startsWith("text/") || n.endsWith(".txt") || n.endsWith(".md")) return "📄";
    if (n.endsWith(".zip") || n.endsWith(".tar") || n.endsWith(".gz")) return "🗜️";
    return "📎";
  }

  // 아바타 초기화
  if (window.USER_AVATAR) {
    userAvatar.style.backgroundImage = `url(${window.USER_AVATAR})`;
    userAvatar.textContent = "";
  } else {
    userAvatar.textContent = (window.USER_NAME || "U").charAt(0).toUpperCase();
  }

  // ---------- Markdown 설정 ----------
  if (window.marked) {
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false,
    });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderMarkdown(text) {
    if (!text) return "";
    if (!window.marked || !window.DOMPurify) {
      // CDN 로드 전 폴백 — 그냥 escape하고 줄바꿈만
      return `<p>${escapeHtml(text).replace(/\n/g, "<br>")}</p>`;
    }
    const raw = marked.parse(text);
    return DOMPurify.sanitize(raw, {
      ADD_ATTR: ["target", "rel"],
    });
  }

  function enhanceContent(rootEl) {
    if (!rootEl) return;
    // 모든 외부 링크에 target/rel
    rootEl.querySelectorAll("a[href]").forEach((a) => {
      if (!/^https?:\/\//i.test(a.getAttribute("href") || "")) return;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    });
    // 코드 블록 — syntax highlight + 복사 버튼
    rootEl.querySelectorAll("pre > code").forEach((code) => {
      if (window.hljs && !code.dataset.highlighted) {
        try {
          hljs.highlightElement(code);
        } catch (_) {}
        code.dataset.highlighted = "1";
      }
      const pre = code.parentElement;
      if (pre && !pre.querySelector(".copy-btn")) {
        const btn = document.createElement("button");
        btn.className = "copy-btn";
        btn.type = "button";
        btn.textContent = "복사";
        btn.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(code.textContent || "");
            btn.textContent = "복사됨";
            setTimeout(() => (btn.textContent = "복사"), 1500);
          } catch (_) {
            btn.textContent = "실패";
          }
        });
        pre.appendChild(btn);
      }
    });
    // 표 — 가로 스크롤 래퍼
    rootEl.querySelectorAll("table").forEach((table) => {
      if (table.parentElement && table.parentElement.classList.contains("table-wrap")) return;
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }

  // ---------- 대화 목록 ----------
  async function loadConversations() {
    const res = await fetch("/chat/api/conversations");
    const data = await res.json();
    convList.innerHTML = "";
    data.conversations.forEach((c) => {
      const item = document.createElement("div");
      item.className = "conv-item" + (c.id === currentConvId ? " active" : "");
      item.dataset.id = c.id;
      item.innerHTML = `
        <span class="conv-item-title">${escapeHtml(c.title)}</span>
        <button class="conv-item-del" title="삭제" aria-label="삭제">×</button>
      `;
      item.addEventListener("click", (e) => {
        if (e.target.classList.contains("conv-item-del")) return;
        openConversation(c.id);
      });
      item.querySelector(".conv-item-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("이 대화를 삭제할까요?")) return;
        await fetch(`/chat/api/conversations/${c.id}`, { method: "DELETE" });
        if (currentConvId === c.id) {
          currentConvId = null;
          renderEmpty();
        }
        loadConversations();
      });
      convList.appendChild(item);
    });
  }

  // ---------- 메시지 렌더링 ----------
  function renderEmpty() {
    messagesEl.innerHTML = "";
    messagesEl.appendChild(emptyState);
    emptyState.style.display = "block";
    titleEl.textContent = "MOCO Chat";
  }

  function clearEmpty() {
    if (emptyState.parentElement === messagesEl) {
      messagesEl.removeChild(emptyState);
    }
  }

  function renderAttachmentsBlock(attachments) {
    if (!attachments || !attachments.length) return "";
    const items = attachments.map((a) => {
      const icon = iconForMime(a.mimetype, a.name);
      const size = formatSize(a.size);
      // 서버 저장 파일명 (stored_name) 이 있을 때만 다운로드 가능. 전송 직후 낙관적 렌더링에는 없음.
      const target = a.stored_name || null;
      const href = target && currentConvId
        ? `/chat/api/conversations/${currentConvId}/attachments/${encodeURIComponent(target)}`
        : null;
      const inner = `<span>${icon}</span>
        <span class="msg-attachment-name">${escapeHtml(a.name)}</span>
        ${size ? `<span class="msg-attachment-size">${escapeHtml(size)}</span>` : ""}`;
      return href
        ? `<a class="msg-attachment" href="${escapeHtml(href)}" target="_blank" rel="noopener" download>${inner}</a>`
        : `<span class="msg-attachment">${inner}</span>`;
    }).join("");
    return `<div class="msg-attachments">${items}</div>`;
  }

  function appendMessage(role, content, attachments) {
    clearEmpty();
    const msg = document.createElement("div");
    msg.className = `msg ${role}`;
    const avatar = role === "user" ? (window.USER_NAME || "U").charAt(0).toUpperCase() : "M";
    msg.innerHTML = `
      <div class="msg-avatar">${escapeHtml(avatar)}</div>
      <div class="msg-body">
        <div class="msg-role">${escapeHtml(role === "user" ? window.USER_NAME || "User" : "MOCO")}</div>
        <div class="msg-content"></div>
      </div>
    `;
    const contentEl = msg.querySelector(".msg-content");
    const attachmentsHtml = renderAttachmentsBlock(attachments);
    contentEl.innerHTML = attachmentsHtml + renderMarkdown(content);
    enhanceContent(contentEl);
    messagesEl.appendChild(msg);
    scrollToBottomIfNear();
    return msg;
  }

  async function openConversation(convId) {
    if (isStreaming) return;
    currentConvId = convId;
    const res = await fetch(`/chat/api/conversations/${convId}/messages`);
    const data = await res.json();
    messagesEl.innerHTML = "";
    titleEl.textContent = data.conversation.title;
    if (data.messages.length === 0) {
      renderEmpty();
    } else {
      data.messages.forEach((m) => appendMessage(m.role, m.content, m.attachments));
      scrollToBottom();
    }
    loadConversations();
  }

  async function newConversation() {
    if (isStreaming) return;
    const res = await fetch("/chat/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    currentConvId = data.id;
    renderEmpty();
    titleEl.textContent = data.title;
    loadConversations();
  }

  // ---------- 메시지 전송 + SSE 수신 ----------
  async function sendMessage(text, agentKey) {
    if (isStreaming) return;
    const hasFiles = pendingFiles.length > 0;
    if (!text.trim() && !hasFiles) return;

    if (!currentConvId) {
      const res = await fetch("/chat/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      currentConvId = data.id;
      loadConversations();
    }

    // 첨부 파일을 user 메시지에 즉시 미리보기로 표시 (서버 응답 전)
    const filesSnapshot = pendingFiles.slice();
    const attachmentsPreview = filesSnapshot.map((f) => ({
      name: f.name,
      mimetype: f.type,
      size: f.size,
    }));
    appendMessage("user", text, attachmentsPreview);
    input.value = "";
    autoResize();
    // 전송 시작 — 다음 메시지에 영향 가지 않도록 미리 클리어
    pendingFiles = [];
    renderPendingFiles();
    isStreaming = true;
    sendBtn.disabled = true;
    attachBtn.disabled = true;

    const assistantMsg = appendMessage("assistant", "");
    const contentEl = assistantMsg.querySelector(".msg-content");
    const toolStatuses = document.createElement("div");
    toolStatuses.className = "tool-statuses";
    contentEl.appendChild(toolStatuses);
    const textBuffer = { value: "" };
    const textEl = document.createElement("div");
    textEl.className = "stream-text";
    contentEl.appendChild(textEl);
    const cursor = document.createElement("span");
    cursor.className = "typing-dot";
    contentEl.appendChild(cursor);

    try {
      let resp;
      if (filesSnapshot.length > 0) {
        const fd = new FormData();
        fd.append("text", text);
        if (agentKey) fd.append("agent", agentKey);
        filesSnapshot.forEach((f) => fd.append("files", f, f.name));
        resp = await fetch(`/chat/api/conversations/${currentConvId}/stream`, {
          method: "POST",
          body: fd,
        });
      } else {
        const body = { text };
        if (agentKey) body.agent = agentKey;
        resp = await fetch(`/chat/api/conversations/${currentConvId}/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!chunk.startsWith("data: ")) continue;
          const payload = chunk.slice(6);
          try {
            const ev = JSON.parse(payload);
            handleEvent(ev, { textBuffer, textEl, toolStatuses, contentEl });
          } catch (e) {
            console.warn("SSE parse error", e, payload);
          }
        }
        scrollToBottomIfNear();
      }
    } catch (e) {
      textEl.innerHTML += `<p class="error">⚠️ 연결 오류: ${escapeHtml(e.message)}</p>`;
    } finally {
      cursor.remove();
      // 최종 렌더 — 코드 하이라이트/복사 버튼/표 래핑 적용
      enhanceContent(contentEl);
      isStreaming = false;
      sendBtn.disabled = false;
      attachBtn.disabled = false;
      loadConversations(); // 제목 업데이트 반영
    }
  }

  // ---------- 첨부 파일 관리 ----------
  function renderPendingFiles() {
    pendingFilesEl.innerHTML = "";
    if (pendingFiles.length === 0) {
      pendingFilesEl.hidden = true;
      return;
    }
    pendingFilesEl.hidden = false;
    pendingFiles.forEach((f, idx) => {
      const chip = document.createElement("div");
      chip.className = "pending-file";
      chip.innerHTML = `
        <span class="pending-file-icon">${iconForMime(f.type, f.name)}</span>
        <span class="pending-file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="pending-file-size">${escapeHtml(formatSize(f.size))}</span>
        <button class="pending-file-remove" type="button" aria-label="제거">×</button>
      `;
      chip.querySelector(".pending-file-remove").addEventListener("click", () => {
        pendingFiles.splice(idx, 1);
        renderPendingFiles();
      });
      pendingFilesEl.appendChild(chip);
    });
  }

  function addFiles(fileList) {
    if (!fileList) return;
    for (const f of fileList) {
      if (pendingFiles.length >= MAX_FILES) {
        alert(`첨부 파일은 최대 ${MAX_FILES}개까지 가능합니다.`);
        break;
      }
      if (f.size > MAX_FILE_SIZE) {
        alert(`'${f.name}' 파일이 너무 큽니다 (최대 50MB).`);
        continue;
      }
      pendingFiles.push(f);
    }
    renderPendingFiles();
  }

  attachBtn.addEventListener("click", () => {
    if (isStreaming) return;
    fileInput.click();
  });
  fileInput.addEventListener("change", (e) => {
    addFiles(e.target.files);
    fileInput.value = "";  // 같은 파일 재선택 가능하도록
  });

  // 드래그 & 드롭 — composer 영역
  const composerEl = document.querySelector(".composer");
  if (composerEl) {
    ["dragenter", "dragover"].forEach((ev) => {
      composerEl.addEventListener(ev, (e) => {
        if (e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files")) {
          e.preventDefault();
          composerEl.classList.add("drag-over");
        }
      });
    });
    ["dragleave", "drop"].forEach((ev) => {
      composerEl.addEventListener(ev, (e) => {
        e.preventDefault();
        composerEl.classList.remove("drag-over");
      });
    });
    composerEl.addEventListener("drop", (e) => {
      if (isStreaming) return;
      if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
    });
  }

  // 클립보드 붙여넣기 — 입력창에서 Cmd/Ctrl+V 로 이미지 등 첨부
  input.addEventListener("paste", (e) => {
    if (!e.clipboardData) return;
    const items = e.clipboardData.items;
    const files = [];
    for (const it of items) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  });

  function handleEvent(ev, ctx) {
    if (ev.type === "text") {
      ctx.textBuffer.value += ev.delta;
      ctx.textEl.innerHTML = renderMarkdown(ctx.textBuffer.value);
    } else if (ev.type === "tool_use") {
      const tag = document.createElement("span");
      tag.className = "tool-status";
      tag.textContent = ev.name;
      ctx.toolStatuses.appendChild(tag);
    } else if (ev.type === "done") {
      if (ev.final && ev.final !== ctx.textBuffer.value) {
        ctx.textBuffer.value = ev.final;
        ctx.textEl.innerHTML = renderMarkdown(ev.final);
      }
    } else if (ev.type === "error") {
      ctx.textEl.innerHTML += `<p class="error">⚠️ ${escapeHtml(ev.message)}</p>`;
    }
  }

  // ---------- 스크롤 ----------
  function isNearBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;
  }
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function scrollToBottomIfNear() {
    if (isNearBottom()) scrollToBottom();
  }
  function updateScrollBtn() {
    scrollBtn.classList.toggle("visible", !isNearBottom());
  }
  messagesEl.addEventListener("scroll", updateScrollBtn);
  scrollBtn.addEventListener("click", scrollToBottom);

  // ---------- 입력창 ----------
  function autoResize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }
  input.addEventListener("input", autoResize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input.value);
    }
  });
  sendBtn.addEventListener("click", () => sendMessage(input.value));

  // 추천 프롬프트
  document.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => sendMessage(btn.dataset.prompt));
  });

  // ---------- 에이전트 모달 ----------
  // AGENT_CATALOG 은 /chat/api/agents 에서 동적으로 받아온다.
  // 빌트인(Atticus, RA Expert) + agent_factory 가 publish 한 generated 에이전트 모두 포함.
  let AGENT_CATALOG = {};

  const modal = $("agent-modal");
  const modalTitle = $("agent-modal-title");
  const modalDesc = $("agent-modal-desc");
  const modalExamples = $("agent-modal-examples");
  const modalInput = $("agent-modal-input");
  const modalSubmit = $("agent-modal-submit");
  const modalCancel = $("agent-modal-cancel");
  const modalClose = $("agent-modal-close");
  let currentAgentKey = null;

  function renderAgentCards() {
    const container = document.querySelector(".agent-cards");
    if (!container) return;
    container.innerHTML = "";
    Object.values(AGENT_CATALOG).forEach((meta) => {
      const btn = document.createElement("button");
      btn.className = "agent-card";
      btn.type = "button";
      btn.dataset.agent = meta.agent_id;
      btn.innerHTML = `
        <span class="agent-card-icon">${escapeHtml(meta.icon || "🤖")}</span>
        <div class="agent-card-body">
          <div class="agent-card-name">${escapeHtml(meta.agent_name || meta.agent_id)}</div>
          <div class="agent-card-desc">${escapeHtml(meta.description || "")}</div>
        </div>
        <span class="agent-card-arrow">→</span>
      `;
      btn.addEventListener("click", () => openAgentModal(meta.agent_id));
      container.appendChild(btn);
    });
  }

  async function loadAgentCatalog() {
    try {
      const res = await fetch("/chat/api/agents");
      if (!res.ok) return;
      const data = await res.json();
      AGENT_CATALOG = {};
      (data.agents || []).forEach((a) => {
        AGENT_CATALOG[a.agent_id] = a;
      });
      renderAgentCards();
    } catch (e) {
      console.warn("agent catalog load failed", e);
    }
  }

  function openAgentModal(agentKey) {
    const meta = AGENT_CATALOG[agentKey];
    if (!meta) return;
    currentAgentKey = agentKey;
    modalTitle.textContent = `${meta.icon || ""} ${meta.agent_name || meta.agent_id}`;
    modalDesc.textContent = meta.description || "";
    modalExamples.innerHTML = "";
    (meta.examples || []).forEach((ex) => {
      const btn = document.createElement("button");
      btn.className = "modal-example";
      btn.type = "button";
      btn.textContent = ex;
      btn.addEventListener("click", () => {
        closeAgentModal();
        sendMessage(ex, agentKey);
      });
      modalExamples.appendChild(btn);
    });
    modalInput.value = "";
    modal.hidden = false;
    setTimeout(() => modalInput.focus(), 50);
  }

  function closeAgentModal() {
    modal.hidden = true;
    currentAgentKey = null;
  }

  modalClose.addEventListener("click", closeAgentModal);
  modalCancel.addEventListener("click", closeAgentModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeAgentModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeAgentModal();
  });

  modalSubmit.addEventListener("click", () => {
    const text = modalInput.value.trim();
    if (!text || !currentAgentKey) return;
    const agentKey = currentAgentKey;
    closeAgentModal();
    sendMessage(text, agentKey);
  });
  modalInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      modalSubmit.click();
    }
  });

  newChatBtn.addEventListener("click", newConversation);
  sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("collapsed"));

  // 초기 로드
  loadConversations();
  loadAgentCatalog();
  updateScrollBtn();

  // 새로 publish 된 에이전트를 새로고침 없이 반영:
  // - 60초 주기 폴링 (탭이 활성 상태일 때만)
  // - 탭 활성화 복귀 시 즉시 1회 재조회
  const AGENT_CATALOG_REFRESH_MS = 60_000;
  setInterval(() => {
    if (document.visibilityState === "visible") loadAgentCatalog();
  }, AGENT_CATALOG_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadAgentCatalog();
  });
})();
