# Usage

## Signing in

Open Hivegent in your browser and choose Sign In.
You are sent to your organization's login page and returned to the app once authenticated.
Your name and a menu for settings and sign out appear in the top bar.

## Chatting with your documents

The chat workspace works like any chat program.

1. Type your question into the input box at the bottom.
2. Press Enter or the send button.
3. The answer streams in below, and the documents the assistant used appear in the context panel.

Behind the scenes the agent searches your documents, reads the relevant passages, and may take several steps before answering.
You can keep typing while it responds. Extra messages are queued and sent as the next turn, which lets you steer a long answer.

### Good versus weak questions

Ask specific questions about concrete topics.

- Good: "What does our privacy policy say about cookies?"
- Good: "Which installation steps does the manual describe?"
- Weak: "Show me everything" (too broad)
- Weak: "Info" (too vague)

## Sources and context

Every answer is grounded in your material, and the context panel shows the documents and passages the assistant pulled in.
Click a document or a passage to open it and see exactly where the information came from.
This makes each answer traceable back to its source.

The assistant searches your documents even when it thinks it already knows the answer.
It reports what a passage says without embellishing it.
If your material does not cover a question, it says so instead of quietly answering from general knowledge.

## Working with documents

The document workspace is where you add and organize your material.

### Uploading

- Drag and drop files, whole folders, or a ZIP archive onto the upload area, or pick them with the file dialog.
- Hivegent converts each file into searchable text, splits it into chunks, and indexes it for retrieval.
- A job tray in the top bar shows the progress of uploads and processing.

Hivegent handles a wide range of common formats, including PDFs, office documents, web pages, e-books, images, and plain text or Markdown.
The exact set depends on your deployment.
You can also create and edit Markdown documents directly in the browser.

### Workspaces and access

Documents live in scopes shown in the document panel.

- Your Documents is your private workspace, which only you can read and write.
- Group workspaces are shared with everyone in a group, with read or write access depending on your membership.

### Managing documents

For each document you can view it, download the original, move it, or delete it.
You can also include or exclude individual documents from the current conversation, so the assistant only draws on the material you want for a given question.

## Conversations

Past conversations are listed in the sidebar with their titles and timestamps.

- Titles are generated automatically and can be edited inline.
- Select a conversation to return to it, or delete one you no longer need.
- Start a new chat at any time with the new chat button.
- Edit one of your earlier messages or regenerate a reply to explore a different direction.
- Export a conversation to a file and import it again later.

When a conversation grows very long, use Compact to summarize the earlier history so it keeps fitting into the model's context.

## Tuning the assistant

Two controls let you shape how the assistant responds.

- Personality: choose Default, Concise, Detailed, or Structured, or supply your own custom system message.
- Reasoning effort: from Auto down to None or up to higher levels, trading depth against speed and cost.

## Memory

The assistant can remember useful facts across separate conversations.
You can clear all saved memory at any time from the settings dialog.

## Beyond the web interface

Power users can reach the same backend without the browser.

- MCP: when the operator enables the MCP endpoint, MCP-capable clients such as code editors or other agents can use a subset of Hivegent's document tools. They connect to the `/mcp` URL and authenticate through the same OIDC login.
- API and command line: the backend exposes a REST API, and the bundled `hivegent` command-line tool can log in and upload, list, download, or delete documents for scripted workflows.
