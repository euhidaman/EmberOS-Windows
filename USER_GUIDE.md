# EmberOS User Guide (Windows Edition)

> **Version 1.0.0**  
> *A Local Agentic Intelligence Layer for your Operating System*

---

## 🧭 What Is EmberOS?

EmberOS is a local AI desktop assistant for Windows that operates entirely on your physical machine. It intelligently parses your requests to help you natively control your system, manage files, start commands, and automate arbitrary tasks using simple, pure natural language. By avoiding the cloud entirely, EmberOS establishes a hyper-local operation philosophy built for privacy.

---

## 🏛️ Core Philosophy

### 🔒 Privacy First
- **Local-first computation**: No reliance on third-party servers.  
- **No mandatory cloud dependency**: Full offline operability for core integrations.  
- **Zero telemetry**: Your personal usage data legitimately stays completely managed under your control.

### ⚡ Efficient Local Intelligence
EmberOS relies on heavily quantized open-weight inference runners allowing it to effortlessly operate on accessible consumer-grade hardware whilst maintaining practical processing throughput speeds.

### 🛠️ Action-Oriented Assistant
EmberOS goes definitively beyond pure chatbots. It is structured intrinsically to:
- Manipulate bulk file paths locally.
- Launch installed execution packages.
- Query underlying Windows system diagnostics naturally.
- Organize extensive data streams effortlessly.

---

## ⚙️ System Architecture

EmberOS maps perfectly to foundational Windows services:
- **Windows Service**: A highly structured background process listening securely to system interactions.
- **Local IPC/API**: Socket routing for lightweight component communication.
- **PowerShell Pipelines**: Embedded wrapper commands for native execution safety.

### Main Components
1. **EmberOS Service** (EmberOSAgent): The backbone background dispatcher interpreting commands, mapping execution toolchains, and handling your contextual continuity/memory structures.
2. **EmberOS GUI**: Desktop graphical frontend interface designed purely for interactive workflows (.\emberos.bat gui).
3. **EmberOS CLI**: Extensive rich-text REPL utility built explicitly for shell interactivity (.\emberos.bat repl).

---

## 📁 Installation & Storage Map

To guarantee portable accessibility, all local dependencies securely reside under standard structures:

| Directory | Core Purpose                                            |
| --------- | --------------------------------------------------------|
| config\ | Core system configurations and telemetry definitions    |
| data\   | Vector memory stores containing localized system facts  |
| data\backups\ | Rollback snapshots preserved prior to executions  |
| logs\   | Internal tracking outputs to gauge diagnostic failures  |

---

## 🕹️ Primary Workflows

### Starting Operations
* **Start Graphical Interface:** .\emberos.bat gui
* **Start Terminal REPL:** .\emberos.bat repl
* **Startup Background Process:** .\launch.ps1

### Running the Daemon Safely
Whenever direct checks are needed on current stability, check natively in PowerShell:
* **Verify System Health:** Get-Service EmberOSAgent
* **Launch Dead Engine:** Start-Service EmberOSAgent
* **Halt Operating Engine:** Stop-Service EmberOSAgent
* **Reboot Runtime System:** Restart-Service EmberOSAgent

---

## 🪄 Feature Capabilities

### 📂 Dynamic File Management
**"Find all PDFs within my Downloads folder."*  
**"Migrate all photos from Downloads securely into Pictures."*  
**"Initialize a blank development space under Documents mapped to Node.js."*

### 🧠 Deep Note Memory 
* **User**: *"Remember that my local SQL server password defaults to 'root'."*
* **EmberOS**: *"Saved persistently."*
* **User (Months later)**: *"What is my SQL database password again?"*

### 💾 Local Application Handling
**"Launch a secure browser instance pointing to GitHub."*  
**"Turn on Spotify."*

### 💻 System Information Discovery
**"What does my absolute CPU configuration load look like recently?"*  
**"Determine total active RAM usage consumed by Google Chrome."*

### 📂 Complex Multi-Step Tasks
**User**: *"Please organize the miscellaneous files scattered in my Downloads folder by extensions natively."*  
**EmberOS**: *"Detected formats spanning (.pdf, .jpg, .exe, .rar). Prepare to execute local mapping moves?"*  
**User**: *"Yes."*

---

## 🛡️ Protections: Snapshots & Rollbacks

### Predictive Preservation 
Operating against underlying drives natively introduces risk. EmberOS negates issues comprehensively by issuing background context-snapshots directly mapped to data\backups\ before **any** destructive file action occurs iteratively.

**Mistake a procedure? Simple reversal:**
* **User**: "Actually undo that last move entirely."
* **EmberOS**: "Rollback protocol initiated. Restoring 5 file mappings entirely."

---

## 🖥️ Interactive CLI Tools
If electing to use the REPL interface, several explicit directives stand ready:
* :help — Expands complete command listings.
* :status — Renders immediate model states, GPU routing metrics, and local memory footprints instantly.
* :clear — Flushes visible terminal history safely.
* :theme — Flips current ANSI graphical variables predictably.
* :rollback — Reverts the last recognized dangerous file execution command.
* :exit / :quit — Kills current foreground pipeline safely.

---

## 🧰 Troubleshooting Basics

**UI Not Progressing Correctly?**
If graphical assets refuse to render cleanly, fall back temporarily via .\emberos.bat gui.

**Performance or Model Drag Experiencing Bottlenecks?**  
Possible culprits include extended persistent conversations stacking token context lengths heavily. Utilize basic terminal resets or execute Restart-Service EmberOSAgent broadly effectively.

---
