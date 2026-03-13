# EmberOS (Windows Edition)

<p align="left">
  <img src="https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6?style=flat-square&logo=windows" alt="Platform: Windows">
  <img src="https://img.shields.io/badge/AI-Local%20First-4caf50?style=flat-square" alt="Local AI">
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License: MIT">
</p>

**A Local Agentic Intelligence Layer for your Operating System**

EmberOS is an open-source, local-first AI desktop assistant specifically built for Windows. It acts as an integration layer between advanced local LLMs (Large Language Models) and your desktop environment. By allowing you to manage files, launch applications, query system data, and automate repetitive workflows via natural language—without any cloud API dependency—EmberOS ensures your workflow stays fast, robust, and completely private.

## ✨ Key Features

- **🔒 100% Local Privacy:** Everything runs on your hardware. Data is never sent to the cloud.
- **⚡ Super-efficient Runtime:** Built alongside `llama.cpp` and `bitnet.cpp` loading tools to execute small footprint models like the 1.58-bit model configurations.
- **💻 Native Windows Integration:** Deeply integrated intelligently with PowerShell to execute and trace system commands reliably.
- **🛡️ Safeties & Auto-Rollbacks:** Features built-in auto-snapshots prior to any destructive operation. Delete the wrong files? Just ask EmberOS to roll it back.
- **🧠 Continuous Memory:** Keep track of personal facts and persistent preferences spanning throughout conversational sessions via native snapshot storage.
- **🎨 Rich Multi-Modal Interface:** Utilize either the robust native terminal REPL or the convenient desktop GUI.

## 🚀 Quick Start

### Prerequisites
- Windows 10 or Windows 11
- Git
- PowerShell 5.1+
- (Optional) NVIDIA/AMD GPU for hardware-accelerated model running.

### 1. Installation
Clone the codebase to your desired desktop environment:
```powershell
git clone https://github.com/euhidaman/EmberOS-Windows.git
cd EmberOS-Windows
```

### 2. Environment Setup
Initialize the embeddable Python execution environment and download requisite dependencies natively:
```powershell
.\setup.ps1
```

### 3. Launching EmberOS
To boot EmberOS into proper background-service mode and open the system tray functionality:
```powershell
.\launch.ps1
```

## 🎮 Interfaces

Once the backend is live, command your agent easily using either GUI or CLI options:

**Launch the CLI Terminal REPL:**
```powershell
.\emberos.bat repl
```

**Launch the Graphical Desktop UI:**
```powershell
.\emberos.bat gui
```

## 🤖 Example Prompts

EmberOS fundamentally responds to practical, every-day scenarios:
- *"Find all PDFs in my Downloads folder"*
- *"Organize my desktop by creating folders for Images and Documents"*
- *"How much memory is currently available globally?"*
- *"Remember that my Wi-Fi password is 'GuestNetwork2024'"*
- *"Launch Spotify and open Chrome"*

## 📖 External Documentation
- Read the **[User Guide](USER_GUIDE.md)** for exhaustive details on CLI features, troubleshooting, and architectural flows.

## 🏗️ Core Architecture
- **EmberOS Service** (`EmberOSAgent`): Maintains a background event loop and model runner context.
- **CLI/GUI Clients**: Acts purely as front-end channels to issue instructions to the underlying Windows process.
- **Memory File Systems**: Resides natively inside `data\vectors` and basic snapshots inside `data\backups\`.

---

*EmberOS — Private. Local. Efficient.*