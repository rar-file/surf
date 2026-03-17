---
name: File Reader
description: Read and analyze files from the workspace
icon: 📄
enabled: false
author: SURF
version: 1.0
---

# File Reader Skill

You can read files from the workspace directory to help users understand, analyze, or reference their code and documents.

## Capabilities
- Read text files up to 50KB
- Access any file within the workspace directory
- Supports all text-based formats: .py, .js, .md, .txt, .json, .yaml, .html, .css, etc.

## Instructions
When the user asks about a file, wants to review code, or needs file contents:
1. Identify which file(s) the user is referring to
2. Read the file contents
3. Provide relevant analysis, summaries, or answer questions about the content
4. If the file is large, focus on the most relevant sections

## Constraints
- Read-only access — cannot modify files
- Files must be within the workspace directory (security boundary)
- Maximum file size: 50KB
- Binary files are not supported

## Examples
- "What does web_ui.py do?" → Read the file and provide a summary
- "Show me the contents of requirements.txt" → Read and display the file
