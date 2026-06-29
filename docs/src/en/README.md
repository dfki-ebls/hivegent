# Introduction

<div align="center">
  <img src="assets/logo.svg" alt="Hivegent logo" width="256">
</div>

Hivegent is a self-hosted assistant that answers questions about your own documents.
It pairs a familiar chat interface with an agent that can search, read, and reason over the files you upload.

Unlike a plain chatbot, Hivegent does not rely only on what a language model learned during training.
Before it answers, the agent works through your documents and past conversations, decides which tools to use, and cites the sources it relied on.
This is what "retrieval-augmented" and "agentic" mean in practice: answers are grounded in your material, and the assistant can take several steps to find them.

## What you can do

- Chat with your documents in natural language and get answers with source citations.
- Upload PDFs, Office files, images, and more, which Hivegent converts and indexes for you.
- Organize documents in a private workspace and in shared group workspaces with access control.
- Let the agent work through multi-step questions, optionally search the web, and remember context across conversations.
- Connect external tools or your own editor through the Model Context Protocol (MCP).

## How it differs from a plain chatbot

- Up to date: answers draw on your latest documents, not just training data.
- Grounded: every answer can point to the exact passage it came from.
- Agentic: the assistant chooses tools and explores in several steps instead of answering in one shot.
- Private: you run it yourself, so your documents stay on your own infrastructure.

Read [How Hivegent Works](concepts.md) for the ideas behind it, [Architecture](architecture.md) to see how the pieces fit together, [Setup](setup.md) to install it, and [Usage](usage.md) to get started.
