# LocalPilot architecture

All diagrams render directly on GitHub (Mermaid).

## 1. System overview

```mermaid
flowchart TB
    U(["👤 You<br/>Web chat UI (WhatsApp-style) · Terminal CLI"])
    WS["🔌 LocalPilot server<br/>FastAPI + WebSocket · listens on 127.0.0.1 only"]
    TYPO["✏️ Typo fixer<br/>Understood as… / ❓ Did you mean…?"]
    SCHED["⏰ Scheduler<br/>cron · every N min · once"]
    AGENT["🤖 Agent loop<br/>plan → pick tool → check → run → repeat"]
    QWEN[("🧠 Qwen3 via Ollama<br/>chat + tool calling")]
    LOOP["🔁 Loop guard<br/>15 steps · no duplicates · stop after 3 errors"]
    RULES{"🛡 Safety rules<br/>READ · WRITE · HIGH · BLOCKED"}
    LAYA["🛡 LAYA guard (optional)<br/>escalate-only second opinion"]
    LAYAM[("LAYA decision model<br/>421M · ~33 ms")]
    CARD["✅ Approval / question card<br/>Allow once · Always · Deny"]
    BLOCK["⛔ Refused"]
    TOOLS["🧰 Tools<br/>🔍 search · 🌐 pages · 🧭 browser · 📁 files<br/>⌨️ PowerShell · 🌿 git · ⬇️ installs · 🖥 system"]
    NET(("🌍 Internet<br/>web tools only"))
    AUDIT[("📜 Activity log")]

    U <--> WS
    WS --> TYPO --> AGENT
    SCHED --> AGENT
    AGENT <--> QWEN
    AGENT --- LOOP
    AGENT --> RULES
    RULES -- BLOCKED --> BLOCK
    RULES --> LAYA
    LAYA <-.-> LAYAM
    LAYA -- READ --> TOOLS
    LAYA -- "WRITE / HIGH" --> CARD
    CARD -- shown to --> U
    CARD -- Allow --> TOOLS
    TOOLS <--> NET
    TOOLS -- "output (injection scan)" --> LAYA
    LAYA -- result --> AGENT
    AGENT --> AUDIT

    classDef you fill:#d9fdd3,stroke:#00a884,color:#111b21
    classDef core fill:#ffffff,stroke:#008069,color:#111b21
    classDef safe fill:#fff4d6,stroke:#f2a900,color:#111b21
    classDef model fill:#e7f0ff,stroke:#3b6fd8,color:#111b21
    classDef bad fill:#fde2e2,stroke:#ea0038,color:#111b21
    class U,CARD you
    class WS,TYPO,SCHED,AGENT,LOOP,TOOLS,AUDIT core
    class RULES,LAYA safe
    class QWEN,LAYAM,NET model
    class BLOCK bad
```

## 2. What happens when you send a message

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant UI as Chat UI
    participant S as Server
    participant F as Typo fixer
    participant A as Agent loop
    participant M as Qwen3 (Ollama)
    participant G as Safety rules + LAYA
    participant T as Tool

    U->>UI: "isntall gti and clne my repo"
    UI->>S: chat (WebSocket)
    S->>F: fix typos
    F->>M: correct this message (JSON)
    M-->>F: {"text": "install git and clone my repo"}
    alt meaning is unclear
        F-->>UI: ❓ Did you mean A / B / as typed?
        U->>UI: taps an option
    else clear
        F-->>UI: ✏️ Understood as: install git and clone my repo
    end
    loop until answered (max 15 steps)
        A->>M: history + tools
        M-->>A: tool call, e.g. install_package(winget, Git.Git)
        A->>G: classify risk
        alt BLOCKED
            G-->>A: refused (never runs)
        else READ
            G-->>A: run now
        else WRITE / HIGH
            G-->>UI: approval card with exact command
            U->>UI: Allow / Deny
        end
        A->>T: run
        T-->>A: output
        A->>G: LAYA scans web/file output for prompt-injection
        A-->>UI: tool card (✓ done / ✗ failed)
    end
    M-->>A: final answer
    A-->>UI: reply bubble
```

## 3. Safety decision for every action

```mermaid
flowchart TD
    C["Tool call from the model"] --> R{"Rule-based check<br/>safety.py"}
    R -->|"format disk, rm -rf /,<br/>disable antivirus, leak keys"| B["⛔ BLOCKED<br/>never runs, in any mode"]
    R -->|"ls, git status, read file, search"| RD[READ]
    R -->|"write file, mkdir, commit, run script"| W[WRITE]
    R -->|"delete, install, push, sudo, registry,<br/>outside allowed folders, secrets"| H[HIGH]

    RD --> L{"LAYA installed?"}
    W --> L
    L -->|no| M
    L -->|"yes: destructive ≥ 85%"| UP["escalate one level<br/>READ→WRITE, WRITE→HIGH"] --> M
    L -->|"yes: looks fine"| M
    H --> M{"Mode"}

    M -->|"READ (any mode)"| RUN["✅ run"]
    M -->|"WRITE + Careful"| ASK["🟡 ask: Allow once / Always / Deny"]
    M -->|"WRITE + Trusted<br/>or 'Always allow' this chat"| RUN
    M -->|"HIGH (any mode)"| ASK2["🔴 ask every single time"]
    M -->|"WRITE/HIGH + Read-only"| NO["denied"]
    ASK -->|Allow| RUN
    ASK2 -->|Allow| RUN
```

## 4. How the model is chosen

```mermaid
flowchart LR
    HW["Detect PC<br/>RAM · NVIDIA VRAM · Apple Silicon"] --> BUD["Memory budget<br/>GPU: VRAM − 1.5 GB<br/>CPU: 50% of RAM"]
    BUD --> Q{budget}
    Q -->|"≥ 20 GB"| M1["qwen3:30b"]
    Q -->|"≥ 10.5 GB"| M2["qwen3:14b"]
    Q -->|"≥ 6 GB"| M3["qwen3:8b ⭐"]
    Q -->|"≥ 3.5 GB"| M4["qwen3:4b"]
    Q -->|"< 3.5 GB"| M5["qwen3:1.7b"]
    M1 & M2 & M3 & M4 & M5 --> INST{"already<br/>downloaded?"}
    INST -->|yes| USE["use it"]
    INST -->|"no, but others are"| BEST["use best installed<br/>+ suggest download"]
    INST -->|nothing installed| PULL["System tab → Download"]
```

## 5. Scheduled tasks

```mermaid
sequenceDiagram
    participant S as Scheduler (APScheduler)
    participant A as Agent loop
    participant UI as Any open chat window
    S->>A: time reached → run task prompt (fresh session)
    A->>UI: ⏰ events labelled with the task name
    alt action needs approval
        A->>UI: approval card
        UI-->>A: Allow / Deny (or timeout after 15 min → skipped safely)
    end
    A->>S: result saved (Tasks tab shows last result)
```

## Module map

| File | Job |
|---|---|
| `agent/server.py` | FastAPI app, WebSocket chat, approvals/questions, REST API, local-only guards |
| `agent/core.py` | Agent loop, system prompt, context window, tool execution, `ask_user` handling |
| `agent/safety.py` | Rule-based risk classifier for shell commands and paths; modes |
| `agent/laya_guard.py` | Optional LAYA second opinion (injection scan, destructive check) |
| `agent/loopguard.py` | Step budget, duplicate-call and repeated-error detection |
| `agent/typofix.py` | Typo correction, "did you mean" options, path suggestions |
| `agent/llm.py` | Ollama + OpenAI-compatible clients, text tool-call fallback |
| `agent/hardware.py` | PC detection and model recommendation |
| `agent/scheduler.py` | Persistent cron / interval / one-time tasks |
| `agent/tools/*` | web, files, system (shell, git, installs), schedule, interact |
| `web/*` | Chat UI (plain HTML/CSS/JS, no build step) |
