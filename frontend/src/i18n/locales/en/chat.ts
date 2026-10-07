/** The chat: transcript, composer, tool cards, conversation history, and AI transparency. */
export const chat = {
  layout: {
    documents: "Documents",
    documentsDescription: "Browse context and documents",
    viewDocuments: "View Documents",
  },
  header: {
    chat: "Chat",
    history: "History",
    import: "Import conversation from JSON",
    importFile: "Import conversation file",
    compact: "Compact conversation",
    export: "Export conversation as JSON",
    newChat: "New chat",
  },
  sidebar: {
    imported: "Conversation imported",
    approvalBlocked: "Available once the current response finishes.",
    toolDenied:
      "The user rejected this tool call, so it was not executed. Do not call the same tool again with the same or similar arguments. Stop working on this step, tell the user what you were about to do, and wait for their instructions.",
  },
  messages: {
    emptyTitle: "Ask about your documents",
    emptyDescription: "Start a conversation to search and explore your documents.",
    scrollToEnd: "Scroll to end",
    dismiss: "Dismiss",
    submitEdit: "Submit",
    attachedImage: "Attached image",
  },
  reasoning: {
    thinking: "Thinking …",
    thoughtBriefly: "Thought for a few seconds",
    thoughtFor_one: "Thought for {{count}} second",
    thoughtFor_other: "Thought for {{count}} seconds",
  },
  contextLimit: {
    title: "Context limit reached",
    message: "Compact this conversation to summarize its history, then retry the last message.",
    action: "Compact and retry",
  },
  compaction: {
    bannerTitle: "Continued conversation",
    bannerText: "This conversation was compacted from a <previous>previous chat</previous>.",
    compacting: "Compacting conversation",
    compactingDescription: "Summarizing earlier messages to fit the context window.",
    compacted: "Conversation compacted",
    compactedDescription: "Open it to continue where this chat left off.",
    failed: "Couldn’t compact the conversation",
  },
  navGuard: {
    title: "Stop the current response?",
    description:
      "This chat is still generating a response. Leaving now stops it. Any partial answer is saved, so you can return and continue this conversation later.",
    stay: "Stay",
    leave: "Leave and stop",
  },
  steering: {
    queued_one: "queued message",
    queued_other: "queued messages",
  },
  composer: {
    placeholder: "Ask about your documents …",
    steerPlaceholder: "Steer the conversation …",
    submit: "Submit",
    stop: "Stop",
    dictate: "Dictate",
    attachImage: "Attach image",
    attachImageHint: "Attach an image for the assistant to look at",
    removeAttachment: "Remove {{name}}",
    removeImage: "Remove image",
    removeFilter: "Remove {{name}} from the filter",
    attachmentErrors: {
      accept:
        "Only images can be attached. Upload other documents to your workspace, where the assistant can search them.",
      maxFileSize: "Images must be under {{size}}.",
      maxFiles_one:
        "At most {{count}} image can be attached, since the model server accepts no more in one request.",
      maxFiles_other:
        "At most {{count}} images can be attached, since the model server accepts no more in one request.",
    },
  },
  suggestions: {
    overview: {
      label: "Workspace overview",
      prompt:
        "Give me a high-level overview of my document collection. List the main folders or categories and briefly describe the kinds of business documents in each.",
    },
    actionItems: {
      label: "Open action items",
      prompt:
        "Search across my documents for open action items, tasks, and follow-ups. List each one with who is responsible and any mentioned deadline.",
    },
    deadlines: {
      label: "Upcoming deadlines",
      prompt:
        "Find the key dates and deadlines mentioned across my documents and list them in chronological order, noting which document each one comes from.",
    },
    decisions: {
      label: "Key decisions",
      prompt:
        "Identify the important decisions recorded across my documents. For each one, summarize what was decided and reference the document and date.",
    },
    risks: {
      label: "Risks & blockers",
      prompt:
        "Search my documents for any risks, blockers, or open issues that have been raised, and summarize them grouped by topic.",
    },
  },
  conversations: {
    loading: "Loading conversations …",
    untitled: "Untitled",
    titleLabel: "Conversation title",
    editTitle: "Edit title",
    generateTitle: "Generate title with AI",
    delete: "Delete conversation",
    search: "Search conversations …",
    emptyTitle: "No conversations yet",
    emptyDescription: "Start a new chat to begin",
    noMatchesTitle: "No matching conversations",
    noMatchesDescription: "Try a different search term",
    deleteTitle: "Delete conversation?",
    deleteNamed:
      "This will permanently delete “{{title}}” and all its messages. This action cannot be undone.",
    deleteUnnamed:
      "This will permanently delete this conversation and all its messages. This action cannot be undone.",
  },
  disclosure: {
    notice: "Hivegent is AI and can make mistakes. Please double-check responses.",
  },
  transparency: {
    trigger: "Verify AI text",
    title: "Verify Hivegent text",
    description:
      "Check text for Hivegent’s imperceptible watermark. The text is processed only for this check and is not retained.",
    textLabel: "Text",
    textDescription:
      "For a reliable negative result, use more than {{tokens}} tokens. Watermarks can be damaged by editing or translation, so a negative result never proves human authorship.",
    placeholder: "Paste the text to verify …",
    resultLabel: "Result",
    resultDescription:
      "Based on an imperceptible text watermark. The signed report contains only a SHA-256 hash of the submitted text and detector metadata.",
    contact: "Qualified external reviewers can request access at <email />.",
    download: "Download Signed Report",
    verify: "Verify Text",
    verifying: "Verifying …",
  },
  citation: {
    openDocument: "Open the current document",
    openEvidence: "Open the captured lines",
    noEvidence: "No supporting tool output was captured for these lines.",
    evidenceDescription:
      "{{position}} from the tool output stored with this conversation. The document link opens the current workspace version separately.",
    capturedBy: "Captured by {{source}}",
  },
  image: {
    unavailable: "[Image not available: {{src}}]",
  },
  tools: {
    names: {
      delete_document: "Delete Document",
      edit_document: "Edit Document",
      explore: "Explore",
      get_conversation: "Read Conversation",
      glob_documents: "Find Documents",
      grep: "Search Text",
      jq: "Query JSON",
      list_conversations: "List Conversations",
      list_documents: "List Documents",
      move_document: "Move Document",
      query_table: "Query Table",
      read_binary_document: "Read Binary Document",
      read_document: "Read Document",
      run_python: "Run Python",
      save_memory: "Save Memory",
      search: "Semantic Search",
      web_fetch: "Fetch Web Page",
      web_search: "Web Search",
      write_document: "Write Document",
    },
    status: {
      "approval-requested": "Awaiting Approval",
      "approval-responded": "Responded",
      "input-available": "Running",
      "input-streaming": "Pending",
      "output-available": "Completed",
      "output-denied": "Denied",
      "output-error": "Error",
    },
    sections: {
      parameters: "Parameters",
      result: "Result",
      steps: "Steps",
      program: "Program",
    },
    approval: {
      question: "Allow the assistant to run <bold>{{tool}}</bold>?",
      approve: "Approve",
      deny: "Deny",
      approved: "Approved",
      denied: "Denied",
    },
    subagent: {
      reasoning: "Reasoning",
      response: "Response",
    },
    binary: {
      pages_one: "Page {{pages}}",
      pages_other: "Pages {{pages}}",
      frames_one: "{{count}} frame sampled",
      frames_other: "{{count}} frames sampled",
      duration: "{{duration}} s",
      attached: "Attached to model",
      previewUnavailable: "Preview unavailable",
    },
    runPython: {
      titleWithScript: "{{tool}} · {{script}}",
      viewSource: "View source",
      sourceDescription:
        "The script as stored now. It is read afresh on every run, so a later edit may differ from the version this call ran.",
      inlineProgram: "Inline program",
      noProgram: "No Python code or script path was provided.",
      truncated: "Printed output was truncated.",
      returned: "Returned:",
      noOutput: "The program printed nothing and returned no value.",
      savedTo: "Saved to <path>{{path}}</path>.",
      notSaved: "Nothing was saved to <path>{{path}}</path>.",
      destination: "Output destination: <path>{{path}}</path>.",
      approvalQuestion: "Allow this program to create or replace <path>{{path}}</path>?",
      approvalHint:
        "Python runs in an isolated sandbox. It can read your documents and use the assistant’s search tools. Saving the document named here requires your approval. Review the program first.",
    },
  },
} as const;
