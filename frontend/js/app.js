/* Prag 知识工作台的前端交互，负责认证、问答、文档和设置页面。 */
(() => {
  const state = {
    token: localStorage.getItem("prag_token"),
    user: null,
    authMode: "login",
    sending: false,
    pollers: new Map()
  };

  const $ = (selector) => document.querySelector(selector);

  const authHeaders = () =>
    state.token ? { Authorization: `Bearer ${state.token}` } : {};

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;"
    }[character]));

  const errorMessage = (detail) =>
    Array.isArray(detail) ? "请检查填写内容" : (detail || "请求未完成，请稍后再试");

  function renderInlineMarkdown(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  }

  function renderMarkdownTable(lines, startIndex) {
    const rows = [];
    let index = startIndex;

    while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
      rows.push(lines[index].trim());
      index += 1;
    }

    const hasDivider = rows.length > 1 && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(rows[1]);
    if (rows.length < 2 || !hasDivider) {
      return null;
    }

    const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    const headers = cells(rows[0]);
    const bodyRows = rows.slice(2).map(cells);
    const head = headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
    const body = bodyRows
      .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
      .join("");

    return {
      html: `<div class="markdown-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`,
      nextIndex: index
    };
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];

    const flushParagraph = () => {
      if (!paragraph.length) {
        return;
      }
      blocks.push(`<p>${renderInlineMarkdown(paragraph.join("\n")).replace(/\n/g, "<br>")}</p>`);
      paragraph = [];
    };

    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const listItem = line.match(/^\s*[-*]\s+(.+)$/);
      const orderedItem = line.match(/^\s*\d+\.\s+(.+)$/);
      const table = renderMarkdownTable(lines, index);

      if (!line.trim()) {
        flushParagraph();
        index += 1;
      } else if (table) {
        flushParagraph();
        blocks.push(table.html);
        index = table.nextIndex;
      } else if (heading) {
        flushParagraph();
        const level = Math.min(heading[1].length + 2, 6);
        blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
      } else if (listItem || orderedItem) {
        const ordered = Boolean(orderedItem);
        const tag = ordered ? "ol" : "ul";
        const items = [];
        while (index < lines.length) {
          const current = lines[index].match(ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*]\s+(.+)$/);
          if (!current) {
            break;
          }
          items.push(`<li>${renderInlineMarkdown(current[1])}</li>`);
          index += 1;
        }
        flushParagraph();
        blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      } else {
        paragraph.push(line);
        index += 1;
      }
    }

    flushParagraph();
    return blocks.join("");
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...authHeaders(), ...(options.headers || {}) }
    });

    if (!response.ok) {
      const requestId = response.headers.get("X-Request-ID") || "未提供";
      let payload = {};
      try {
        payload = await response.json();
      } catch (_) {
        /* 非 JSON 错误响应使用通用提示。 */
      }

      console.error(
        `[Prag API] ${options.method || "GET"} ${path} failed; X-Request-ID: ${requestId}`
      );

      if (response.status === 401) {
        clearSession();
      }

      const error = new Error(errorMessage(payload.detail));
      error.status = response.status;
      throw error;
    }

    if (response.status === 204) {
      return null;
    }

    return response.json();
  }

  function clearSession() {
    state.token = null;
    state.user = null;
    localStorage.removeItem("prag_token");
    const workspace = $("#workspace-view");
    const authView = $("#auth-view");
    const landingView = $("#landing-view");
    if (workspace) {
      workspace.classList.add("is-hidden");
    }
    if (authView) {
      authView.classList.remove("is-hidden");
    } else if (landingView) {
      landingView.classList.remove("is-hidden");
    } else if (!["/", "/login.html", "/register.html"].includes(window.location.pathname)) {
      window.location.href = "/login.html";
    }
  }

  function showWorkspace(user) {
    const workspace = $("#workspace-view");
    const authView = $("#auth-view");
    const landingView = $("#landing-view");
    if (!workspace) {
      window.location.href = "/";
      return;
    }
    state.user = user;
    if (authView) {
      authView.classList.add("is-hidden");
    }
    if (landingView) {
      landingView.classList.add("is-hidden");
    }
    workspace.classList.remove("is-hidden");
    $("#account-email").textContent = user.email;
    $("#account-nickname").textContent = user.nickname || "未设置";
  }

  function setAuthError(message) {
    $("#auth-error").textContent = message || "";
  }

  function toggleAuthMode() {
    state.authMode = state.authMode === "login" ? "register" : "login";
    const register = state.authMode === "register";
    $("#auth-mode-label").textContent = register ? "创建你的工作台" : "欢迎回来";
    $("#auth-submit").textContent = register ? "创建并进入" : "登录工作台";
    $("#confirm-password-field").classList.toggle("is-hidden", !register);
    $("#nickname-field").classList.toggle("is-hidden", !register);
    $("#auth-password").autocomplete = register ? "new-password" : "current-password";
    setAuthError("");
  }

  function focusAuthForm(mode) {
    if (mode !== state.authMode) {
      toggleAuthMode();
    }
    $("#auth-email").focus();
  }

  async function submitAuth(event) {
    event.preventDefault();
    setAuthError("");

    const register = state.authMode === "register";
    const payload = {
      email: $("#auth-email").value.trim(),
      password: $("#auth-password").value
    };

    if (register) {
      const confirmPassword = $("#auth-confirm-password").value;
      if (payload.password !== confirmPassword) {
        setAuthError("两次输入的密码不一致");
        return;
      }
      payload.nickname = $("#auth-nickname").value.trim() || null;
    }

    const button = $("#auth-submit");
    button.disabled = true;

    try {
      const result = await request(`/auth/${register ? "register" : "login"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      state.token = result.access_token;
      localStorage.setItem("prag_token", state.token);
      showWorkspace(result.user);
    } catch (error) {
      setAuthError(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function switchView(view) {
    document.querySelectorAll(".page-view").forEach((page) => {
      page.classList.toggle("is-hidden", page.id !== `${view}-view`);
    });

    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.view === view);
    });

    $("#workspace-view").classList.remove("menu-open");

    if (view === "documents") {
      loadDocuments();
    }
  }

  function addUserMessage(question) {
    $("#messages").querySelector(".welcome-message")?.remove();
    $("#messages").insertAdjacentHTML(
      "beforeend",
      `<article class="message user"><div class="message-bubble">${escapeHtml(question)}</div></article>`
    );
  }

  function renderAssistant(result) {
    const confidence = Math.round((result.confidence || 0) * 100);
    const low =
      result.answer === "文档中没有足够依据" || confidence < 30;

    const citations = (result.citations || [])
      .map((citation) =>
        `<details class="citation"><summary>[${citation.number}] ${escapeHtml(citation.document_name)} · ${citation.page_number ? `第 ${citation.page_number} 页` : `第 ${citation.chunk_index + 1} 段`}</summary><div class="citation-snippet markdown-body">${renderMarkdown(citation.snippet)}</div></details>`
      )
      .join("");

    $("#messages").insertAdjacentHTML(
      "beforeend",
      `<article class="message assistant"><div class="message-bubble markdown-body">${renderMarkdown(result.answer)}</div><div class="confidence ${low ? "low" : ""}">置信度 ${confidence}% · 命中 ${result.retrieval_hit_count} 条依据</div>${low ? `<div class="evidence-warning">文档中没有足够依据，建议补充资料后再问。</div>` : ""}${citations ? `<div class="citations">${citations}</div>` : ""}</article>`
    );

    $("#messages").lastElementChild.scrollIntoView({
      behavior: "smooth",
      block: "nearest"
    });
  }

  async function submitChat(event) {
    event.preventDefault();

    if (state.sending) {
      return;
    }

    const input = $("#question-input");
    const question = input.value.trim();

    if (!question) {
      return;
    }

    state.sending = true;
    $("#send-button").disabled = true;
    addUserMessage(question);
    input.value = "";

    try {
      renderAssistant(
        await request("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question })
        })
      );
    } catch (error) {
      $("#messages").insertAdjacentHTML(
        "beforeend",
        `<article class="message assistant"><div class="message-bubble">${escapeHtml(error.message)}</div></article>`
      );
    } finally {
      state.sending = false;
      $("#send-button").disabled = false;
      input.focus();
    }
  }

  function statusText(status) {
    return (
      {
        uploaded: "等待处理",
        parsing: "解析中",
        indexing: "建立索引",
        ready: "已就绪",
        failed: "处理失败"
      }[status] || status
    );
  }

  function renderDocuments(documents) {
    const container = $("#documents-list");

    if (!documents.length) {
      container.innerHTML = `<div class="welcome-message"><span class="welcome-glyph">⌂</span><h3>资料架还是空的</h3><p>上传第一份文档，开始建立你的可检索知识。</p></div>`;
      return;
    }

    container.innerHTML = documents
      .map((doc) =>
        `<article class="document-row" data-id="${doc.id}"><div class="document-name">${escapeHtml(doc.filename)}</div><div class="document-meta">${doc.file_type.toUpperCase()} · ${formatBytes(doc.file_size)}</div><div class="status-label ${doc.status === "failed" ? "failed" : ""}">${statusText(doc.status)}${doc.parse_error ? `：${escapeHtml(doc.parse_error)}` : ""}</div><div class="row-actions">${doc.status === "failed" ? `<button class="small-button" data-action="reprocess">重新处理</button>` : ""}<button class="small-button danger" data-action="delete">删除</button></div></article>`
      )
      .join("");
  }

  function formatBytes(bytes) {
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    if (bytes < 1048576) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  async function loadDocuments() {
    try {
      const documents = await request("/documents");
      renderDocuments(documents);
      documents
        .filter((doc) => ["uploaded", "parsing", "indexing"].includes(doc.status))
        .forEach(startPolling);
    } catch (error) {
      $("#documents-list").innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
    }
  }

  function startPolling(document) {
    if (state.pollers.has(document.id)) {
      return;
    }

    const timer = setInterval(async () => {
      try {
        const latest = await request(`/documents/${document.id}`);
        if (!["uploaded", "parsing", "indexing"].includes(latest.status)) {
          clearInterval(timer);
          state.pollers.delete(document.id);
          loadDocuments();
        }
      } catch (_) {
        clearInterval(timer);
        state.pollers.delete(document.id);
      }
    }, 3000);

    state.pollers.set(document.id, timer);
  }

  async function uploadDocuments(event) {
    const files = [...event.target.files];

    if (!files.length) {
      return;
    }

    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    $("#upload-feedback").textContent = "正在上传……";

    try {
      const result = await request("/documents/batch-upload", {
        method: "POST",
        body: form
      });

      const failed = result.results.filter((item) => !item.success);
      $("#upload-feedback").textContent = failed.length
        ? `${files.length - failed.length} 份上传成功，${failed.length} 份失败。`
        : `${files.length} 份文档已加入处理队列。`;

      loadDocuments();
    } catch (error) {
      $("#upload-feedback").textContent = error.message;
    } finally {
      event.target.value = "";
    }
  }

  async function documentAction(event) {
    const button = event.target.closest("button[data-action]");

    if (!button) {
      return;
    }

    const id = button.closest(".document-row").dataset.id;

    try {
      if (button.dataset.action === "delete") {
        if (!window.confirm("确定删除这份文档吗？")) {
          return;
        }
        await request(`/documents/${id}`, { method: "DELETE" });
      } else {
        await request(`/documents/${id}/reprocess`, { method: "POST" });
      }

      loadDocuments();
    } catch (error) {
      $("#upload-feedback").textContent = error.message;
    }
  }

  async function changePassword(event) {
    event.preventDefault();

    const current = $("#current-password").value;
    const next = $("#new-password").value;
    const confirm = $("#confirm-password").value;
    const feedback = $("#password-feedback");

    feedback.className = "form-feedback";

    if (next !== confirm) {
      feedback.textContent = "两次输入的新密码不一致";
      feedback.classList.add("error");
      return;
    }

    try {
      const result = await request("/auth/password", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next })
      });

      feedback.textContent = result.message;
      event.target.reset();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add("error");
    }
  }

  async function logout() {
    try {
      if (state.token) {
        await request("/auth/logout", { method: "POST" });
      }
    } catch (_) {
      /* 本地会话仍需清理。 */
    } finally {
      clearSession();
    }
  }

  async function boot() {
    const authForm = $("#auth-form");
    if (authForm) {
      state.authMode = window.location.pathname === "/register.html" ? "register" : "login";
      authForm.addEventListener("submit", submitAuth);
    }

    const chatForm = $("#chat-form");
    if (chatForm) {
      chatForm.addEventListener("submit", submitChat);
    }
    const fileInput = $("#file-input");
    if (fileInput) {
      fileInput.addEventListener("change", uploadDocuments);
    }
    const documentsList = $("#documents-list");
    if (documentsList) {
      documentsList.addEventListener("click", documentAction);
    }
    const passwordForm = $("#password-form");
    if (passwordForm) {
      passwordForm.addEventListener("submit", changePassword);
    }
    const logoutButton = $("#logout-button");
    if (logoutButton) {
      logoutButton.addEventListener("click", logout);
    }
    const menuButton = $("#menu-button");
    if (menuButton) {
      menuButton.addEventListener("click", () =>
        $("#workspace-view").classList.toggle("menu-open")
      );
    }

    document.querySelectorAll(".nav-item").forEach((item) => {
      item.addEventListener("click", () => switchView(item.dataset.view));
    });

    const questionInput = $("#question-input");
    if (questionInput) {
      questionInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          $("#chat-form").requestSubmit();
        }
      });
    }

    if (state.token) {
      try {
        showWorkspace(await request("/auth/me"));
      } catch (_) {
        clearSession();
      }
    }
  }

  boot();
})();