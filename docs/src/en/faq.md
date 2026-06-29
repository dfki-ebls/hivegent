# FAQ

## General

### How is this different from ChatGPT?

A plain chatbot answers only from what its model learned during training.
Hivegent first searches your own documents and past conversations, then answers from them and cites the sources.
It can also take several steps and use tools, so it can explore a question rather than answering in one shot.

### Where do my documents and data live?

You host Hivegent yourself.
Document files stay on your server's disk, and their index, your conversations, and the assistant's memory live in your own PostgreSQL database.
The only data that leaves your infrastructure is what you send to the language model endpoint you configured.

### Which languages are supported?

This depends on how your deployment is configured.
The assistant answers in whatever languages its configured language model handles, and document search and OCR cover the languages set by your operator.
Mixed-language documents are no problem.

### Which file formats can I upload?

A wide range of common formats, including PDFs, office documents, web pages, e-books, images, and plain text or Markdown.
The exact set depends on your deployment, and scanned PDFs are processed with OCR.

## Using the assistant

### Why does it not show all of my documents?

The assistant retrieves only the passages relevant to your question.
A broad request like "list everything" cannot return useful results.
Ask a specific question about a concrete topic instead.

### The assistant cannot find a document I uploaded.

- It may still be processing. Watch the job tray in the top bar and wait until it finishes.
- Check that the document is in the right workspace and is not excluded from the current conversation.
- Confirm the file format is supported and the file is not empty or password protected.

### The answers are inaccurate.

- Ask more precise questions and use wording that appears in your documents.
- Make sure the right documents are present and included in the conversation.
- Well-structured documents with clear headings give better results.
- For a fresher answer, raise the reasoning effort or remove outdated documents.

### Can the assistant use the web?

Only if the operator enables web tools.
By default the assistant answers from your indexed documents alone.

## Access and accounts

### How do I get access to shared documents?

Group workspaces are controlled by your identity provider.
An administrator adds you to a group, and you then get read or write access to that group's shared workspace.

### Who can perform administrative actions?

Only users whose login carries the `admin` role.
Administrators can enable maintenance mode, reindex the workspace, and reset data.

## Operations

### Where do I configure the system?

In the `config.toml` file and through `HIVEGENT_*` environment variables.
See [Setup](setup.md) for the common settings.

### How do I update?

Pull the new image and recreate the containers.
The backend applies any database migrations automatically on startup.

### High CPU or memory usage.

This is normal during document processing, especially OCR on scanned PDFs, and when running a local language model.
To reduce load, process fewer documents at once, use a GPU, or point at a hosted model endpoint.

### When are local models worth it?

It is a trade-off between cost, infrastructure, privacy, and model quality, since the strongest commercial models are only available through their hosted APIs.
Local models keep all data on your infrastructure but need capable hardware.
