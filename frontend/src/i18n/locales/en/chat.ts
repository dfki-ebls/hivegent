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
  },
  messages: {
    emptyTitle: "Ask about your documents",
    emptyDescription: "Start a conversation to search and explore your documents.",
    scrollToEnd: "Scroll to end",
    submitEdit: "Submit",
    editLabel: "Edit message",
    attachedImage: "Attached image",
  },
  reasoning: {
    thinking: "Thinking …",
    thought: "Thought",
    seconds: "{{count}} s",
  },
  steps: {
    took_one: "Took {{count}} step",
    took_other: "Took {{count}} steps",
    failed: "{{count}} failed",
    processing: "Processing …",
  },
  compaction: {
    compacted: "Conversation compacted",
  },
  navGuard: {
    title: "Stop the current response?",
    description:
      "This chat is still generating a response. Leaving now stops it. Any partial answer is saved, so you can return and continue this conversation later.",
    stay: "Stay",
    leave: "Leave and stop",
  },
  steering: {
    queued_one: "{{count}} queued message",
    queued_other: "{{count}} queued messages",
  },
  composer: {
    placeholder: "Ask about your documents …",
    steerPlaceholder: "Steer the conversation …",
    submit: "Submit",
    stop: "Stop",
    dictate: "Dictate",
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
    title: "Verify Hivegent text",
    description:
      "Check text for Hivegent’s imperceptible watermark. The text is processed only for this check and is not retained.",
    textLabel: "Text",
    textDescription:
      "Longer, unedited text improves detection, but no length guarantees a positive result. With fewer than {{tokens}} scored tokens (word pieces, repeated passages may count once), a negative result is inconclusive. Editing or translation can damage the watermark, so a negative result never proves human authorship.",
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
    evidenceDescription:
      "{{position}} from the tool output stored with this conversation. The document link opens the current workspace version separately.",
    capturedBy: "Captured by {{source}}",
  },
  image: {
    unavailable: "[Image not available: {{src}}]",
  },
  tools: {
    names: {
      apply_changes: "Apply Changes",
      ask_user: "Questions",
      delete_documents: "Delete Documents",
      edit_document: "Edit Document",
      explore: "Explore",
      get_conversation: "Read Conversation",
      grep: "Search Text",
      jq: "Query JSON",
      list_conversations: "List Conversations",
      list_documents: "List Documents",
      move_documents: "Move Documents",
      query_table: "Query Tables",
      read_binary_document: "Read Binary Files",
      read_document: "Read Documents",
      read_memory: "Read Memory",
      run_python: "Run Python",
      search: "Semantic Search",
      web_fetch: "Fetch Web Pages",
      web_search: "Web Search",
      write_document: "Write Document",
      write_memory: "Write Memory",
    },
    status: {
      running: "Running",
      stopped: "Stopped",
      failed: "Failed",
      denied: "Denied",
      approved: "Approved",
      awaitingApproval: "Awaiting approval",
    },
    sections: {
      parameters: "Parameters",
      result: "Result",
    },
    approval: {
      question: "Allow the assistant to run <bold>{{tool}}</bold>?",
      approve: "Approve",
      deny: "Deny",
      approved: "Approved",
      denied: "Denied",
      pending_one: "{{count}} action awaits your approval.",
      pending_other: "{{count}} actions await your approval.",
      approveAll: "Approve all",
      denyAll: "Deny all",
      note: "Note for the assistant on {{call}} (optional)",
      notePlaceholder: "For example, why you deny it or what to keep in mind",
      sharedNote: "Note for all actions (optional)",
      yourNote: "Your note:",
      progress: "{{decided}} of {{count}} decided",
      submit: "Submit decisions",
    },
    ask: {
      answered: "Answered",
      recommended: "Recommended",
      other: "Other",
      otherPlaceholder: "Type your own answer",
      note: "Note for the assistant (optional)",
      notePlaceholder: "Anything the assistant should keep in mind",
      yourNote: "Your note:",
      progress: "Question {{current}} of {{count}}",
      unanswered: "Choose or type an answer to continue.",
      previous: "Previous",
      next: "Next",
      dismiss: "Dismiss",
      submit: "Submit answers",
      dismissed: "Dismissed",
    },
    mutations: {
      titleWithPath: "{{tool}} · {{name}}",
      modes: {
        replace: "Replace",
        create: "Create",
        append: "Append",
        prepend: "Prepend",
      },
      emptyContent: "The document is written empty.",
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
      previewUnavailable: "Preview unavailable",
    },
    runPython: {
      titleWithScript: "{{tool}} · {{script}}",
      viewSource: "View source",
      sourceDescription:
        "The script as stored now. It is read afresh on every run, so a later edit may differ from the version this call ran.",
      inlineProgram: "Inline program",
      noProgram: "No Python code or script path was provided.",
      printed: "Printed",
      returned: "Returned",
      noOutput: "The program printed nothing and returned no value.",
      applied_one: "Applied {{count}} change to the workspace.",
      applied_other: "Applied {{count}} changes to the workspace.",
      staged_one: "Staged {{count}} change. Nothing changes until you approve it.",
      staged_other: "Staged {{count}} changes. Nothing changes until you approve them.",
      hostCalls_one: "{{count}} function call",
      hostCalls_other: "{{count}} function calls",
      callFailed: "failed: {{error}}",
    },
    changeset: {
      created: "Created {{path}}",
      updated: "Changed {{path}}",
      moved: "Moved {{source}} to {{destination}}",
      deleted: "Deleted {{path}}",
      mkdir: "Created folder {{path}}",
      replaces: "replaces",
      question_one: "Allow the assistant to apply this change to your workspace?",
      question_other: "Allow the assistant to apply these {{count}} changes to your workspace?",
    },
  },
} as const;
