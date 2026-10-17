# FAQ

## General

### How is this different from ChatGPT?

A plain chatbot answers from its training data.
Hivegent searches your documents first, answers from them in several steps, and cites its sources.

### Where do my documents and data live?

On your own server.
Files stay on disk, and the index, conversations, and memory live in your PostgreSQL database.
Only what you send to the configured language model leaves your infrastructure.

### Which languages are supported?

Whatever your language model handles.
Search and OCR cover the languages your operator configured, and mixed-language documents work.

### Which file formats can I upload?

PDFs, office documents, web pages, e-books, images, and plain text or Markdown, depending on your deployment.
Scanned PDFs are processed with OCR.

## Using the assistant

### Why does it not show all of my documents?

It retrieves only passages relevant to your question, so "list everything" gives poor results.
Ask a specific question instead.

### The assistant cannot find a document I uploaded.

- Wait until the job tray shows that processing has finished.
- Check the workspace and that the document is not excluded from the conversation.
- Check that the format is supported and the file is not empty or password protected.

### The answers are inaccurate.

- Ask precise questions using wording from your documents.
- Make sure the right documents are present and included.
- Use well-structured documents with clear headings.
- Raise the reasoning effort or remove outdated documents.

### Can the assistant use the web?

Only if the operator enables web tools.

## Access and accounts

### How do I get access to shared documents?

An administrator adds you to a group in your identity provider, which grants access to that group's workspace.

### Who can perform administrative actions?

Users with the `admin` role.
They can enable maintenance mode, reindex the workspace, and reset data.

## Operations

### Where do I configure the system?

In `config.toml` and `HIVEGENT_*` environment variables, see [Setup](setup.md).

### How do I update?

Pull the new image and recreate the containers.
Migrations run automatically on startup.

### High CPU or memory usage.

This is normal during document processing, especially OCR, and with a local model.
Process fewer documents at once, use a GPU, or use a hosted model.

### When are local models worth it?

Local models keep all data on your infrastructure but need capable hardware.
The strongest models are only available through hosted APIs.
