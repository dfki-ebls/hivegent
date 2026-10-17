# Usage

## Signing in

Choose Sign In and log in with your organization's account.
The top bar then shows your name and a menu for settings and sign out.

## Chatting with your documents

Type a question and press Enter.
The answer streams in, and the documents the assistant used appear in the context panel.
The agent may take several steps before answering.
Messages you send meanwhile are queued as the next turn, which lets you steer a long answer.

### Good versus weak questions

Ask specific questions about concrete topics.

- Good: "What does our privacy policy say about cookies?"
- Weak: "Show me everything" (too broad) or "Info" (too vague).

## Sources and context

The context panel shows the documents and passages behind each answer.
Click one to see exactly where the information came from.

The assistant always searches your documents and reports what they say without embellishing.
If your material does not cover a question, it says so.

## Working with documents

### Uploading

- Drag files, folders, or a ZIP archive onto the upload area, or use the file dialog.
- Hivegent converts each file to text, splits it into chunks, and indexes it.
- The job tray in the top bar shows progress.

Supported formats include PDFs, office documents, web pages, e-books, images, and plain text or Markdown, depending on your deployment.
You can also create and edit Markdown documents in the browser.

### Workspaces and access

- Your Documents is your private workspace.
- Group workspaces are shared with a group, with read or write access depending on your membership.

### Managing documents

You can view, download, move, or delete each document.
You can also include or exclude documents from the current conversation.

## Conversations

The sidebar lists past conversations.

- Titles are generated automatically and can be edited.
- Open, delete, or start conversations at any time.
- Edit an earlier message or regenerate a reply to try a different direction.
- Export a conversation to a file and import it later.

Use Compact to summarize a long conversation so it fits the model's context.
If the model hits its context limit, a banner offers to compact and retry.

## Tuning the assistant

- Personality: Default, Concise, Detailed, Structured, or a custom system message.
- Reasoning effort: from None to higher levels, or Auto, trading depth against speed and cost.
- Mode: Interactive asks before each workspace change, Write applies changes without asking, and Read allows no changes.

## Memory

The assistant can remember facts across conversations.
Clear all memory at any time in the settings dialog.

## Beyond the web interface

- MCP: if the operator enables it, MCP clients such as editors connect to `/mcp` with the same login.
  They can search, read, explore, and change documents, and every change is shown for confirmation first.
- API and command line: the REST API and the `hivegent` command-line tool can log in and upload, list, download, or delete documents.
