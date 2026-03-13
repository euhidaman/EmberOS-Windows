# EmberOS User Guide (Windows Edition)

Version 1.0.0  
A Local Agentic Intelligence Layer for your Operating System

---

## What Is EmberOS?

EmberOS is a local AI desktop assistant for Windows that runs on your machine.

It helps you control your computer, manage files, run commands, and automate tasks using natural language.

Unlike cloud assistants, EmberOS is designed for local-first operation.

---

## Core Philosophy

### Privacy First

- Local-first processing
- No mandatory cloud dependency for core workflows
- Your data stays under your control

---

### Efficient Local Intelligence

EmberOS is built to run effectively on standard hardware while still delivering practical assistant capabilities for daily desktop tasks.

---

### Action-Oriented Assistant

EmberOS is not just a chatbot.

It can:

- manipulate files
- launch applications
- execute system commands
- automate repetitive workflows

---

## System Architecture (Windows)

EmberOS is designed for Windows-native operation:

- Windows Service for background execution
- Local IPC/API for component communication
- PowerShell and Windows-native tooling for system actions
- Windows-friendly storage layout

---

## EmberOS Components

### 1) EmberOS Service

Background service layer.

Responsible for:

- language understanding
- tool execution
- file system operations
- memory and context management

Service name:

EmberOSAgent

---

### 2) EmberOS GUI

Desktop interface for interactive usage.

Launch command:

.\emberos.bat gui

---

### 3) EmberOS CLI

Terminal interface for PowerShell or Command Prompt.

Launch command:

.\emberos.bat repl

---

## Storage Locations (Default Portable Setup)

Configuration:

config\

Local data:

data\

Snapshots:

data\backups\

Logs:

logs\

---

## Starting EmberOS

### Start GUI

.\emberos.bat gui

### Start CLI REPL

.\emberos.bat repl

### Start Full Background Mode (Service + Tray)

.\launch.ps1

---

## Checking Service Status

Open PowerShell:

Get-Service EmberOSAgent

Start service:

Start-Service EmberOSAgent

Stop service:

Stop-Service EmberOSAgent

Restart service:

Restart-Service EmberOSAgent

---

## File Management

EmberOS can manipulate files across the Windows filesystem.

### Example Requests

Find all PDFs in my Downloads folder

Create a folder called Projects in Documents

Move all images from Downloads to Pictures

What is inside notes.txt?

---

## Notes and Memory

EmberOS includes a local note memory system for saving and recalling important information.

### Example

User:

Remember that the WiFi password is GuestNetwork2024

EmberOS:

Note saved.

Later:

What is the WiFi password?

---

## System Information

EmberOS can query Windows system information using native tools.

### Example Requests

How much disk space do I have left?

Show CPU usage

Show running Python processes

What is my system uptime?

---

## Application Launching

EmberOS can start installed applications by searching common Windows locations.

### Example Requests

Open Chrome

Launch Spotify

Open Notepad

---

## Snapshots and Rollback

Before destructive operations, EmberOS creates a snapshot.

Snapshots can include:

- original files
- metadata
- timestamps

Typical snapshot location:

data\backups\

### Example

User:

Delete all .tmp files

System:

Snapshot created  
Files deleted

Then rollback can restore affected files.

---

## CLI / REPL Commands

:help

Show help.

:status

Show service and runtime status.

:clear

Clear terminal.

:exit

Exit EmberOS.

:theme

Toggle theme in supported interfaces.

:rollback

Rollback the last destructive file operation.

:snapshots

List recent snapshots.

---

## Safety Features

To protect your system, EmberOS asks for confirmation before high-impact actions such as:

- file deletion
- overwrites
- system-level commands
- bulk operations

---

## Example Workflow: Organize Downloads

User:

Organize my Downloads folder by file type

EmberOS:

Detected categories:

- PDFs
- Images
- Documents
- Videos
- Archives

Create folders and move files?

User:

yes

EmberOS executes the operation.

---

## Troubleshooting

### EmberOS not running

Get-Service EmberOSAgent

If stopped:

Start-Service EmberOSAgent

### UI not opening

Start from terminal:

.\emberos.bat gui

### Service needs restart

Restart-Service EmberOSAgent

### Performance issues

Possible causes:

- heavy CPU load
- very large conversation history

Try:

- restarting EmberOS
- clearing active conversation history
- closing heavy background applications

---

## What Makes EmberOS Unique

- private, local-first assistant experience
- practical, action-oriented desktop automation
- deep Windows integration for real task completion

---

## Quick Start

1) Launch EmberOS

.\emberos.bat gui

2) Type a request

Find my budget spreadsheet

3) Review response

4) Confirm high-impact actions

5) Continue the conversation

---

## EmberOS

A Local Agentic Intelligence Layer for your Operating System

Private • Local • Efficient
