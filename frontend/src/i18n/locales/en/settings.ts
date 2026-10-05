/** The settings dialog and the account page. */
export const settings = {
  dialog: {
    title: "Settings",
    description:
      "Configure your LLM provider and assistant settings. These settings are stored locally in your browser.",
    model: {
      label: "Model",
      description: "The main model to use for chat. Leave empty to use the server default.",
      placeholder: "e.g., openai/gpt-4o",
    },
    apiKey: {
      label: "API Key (optional)",
      serverDescription:
        "Server API key configured. Override it here or leave empty to use the server key.",
      description: "Only required for providers that need authentication.",
      serverPlaceholder: "Using server API key",
      placeholder: "Enter your API key (if required)",
    },
    baseUrl: {
      label: "Base URL",
      description: "API endpoint for the LLM provider.",
      placeholder: "e.g., http://localhost:11434/v1",
    },
    auxModel: {
      label: "Auxiliary Model (optional)",
      description:
        "Must be small, fast, and vision-capable. Drives document conversion, alt-text generation, title generation, compaction, subagent exploration, and LLM-guided chunking, all high-volume workloads where cost and latency matter more than reasoning depth. Uses the same provider settings as the main model.",
      placeholder: "e.g., openai/gpt-4o-mini",
    },
    personality: {
      label: "Personality",
      description: "Choose how the assistant responds.",
    },
    customSystemMessage: {
      label: "Custom System Message",
      description: "Provide your own system instructions for the assistant.",
      placeholder: "You are a helpful assistant that …",
    },
    tools: {
      label: "Tools",
      description: "Toggle which tools the assistant can use.",
    },
    toolGroups: {
      explore: "Explore",
      compute: "Compute",
      subagent: "Subagent",
      write: "Write",
      memory: "Memory",
      web: "Web",
      conversation: "Conversation",
      plan: "Plan",
    },
    mcp: {
      label: "MCP Servers",
      description:
        "Connect external tool servers via the Model Context Protocol (Streamable HTTP transport). A prefix namespaces all tools from a server (e.g., prefix “jira” turns “search” into “jira_search”), preventing name collisions when multiple servers provide similarly named tools.",
      prefix: "prefix: {{prefix}}",
      connectionFailed: "Connection failed",
      testConnection: "Test connection",
      removeServer: "Remove server",
      prefixPlaceholder: "Prefix",
      auth: "Auth:",
      authNone: "None",
      authHeaders: "Headers",
      authOAuth2: "OAuth2 Client Credentials",
      headerName: "Header name",
      headerValue: "Value",
      removeHeader: "Remove header",
      addHeader: "Add Header",
      clientId: "Client ID",
      clientSecret: "Client Secret",
      scopes: "Scopes (optional, space-separated)",
      addServer: "Add Server",
    },
    clear: "Clear",
    clearMemory: {
      label: "Clear Memory",
      title: "Clear memory?",
      description:
        "This will permanently delete all saved memory. The assistant will no longer remember information from previous conversations.",
    },
    clearScratch: {
      label: "Clear Scratch",
      title: "Clear scratch files?",
      description:
        "This will delete the working files the assistant parks between tool calls, in your own workspace and every group you can write to. Your documents are untouched.",
      done_one: "Cleared {{count}} scratch file",
      done_other: "Cleared {{count}} scratch files",
    },
    resetDefaults: "Reset to Server Defaults",
  },
  account: {
    title: "Account",
    done: "{{action}}: Done",
    failed: "{{action}}: Failed",
    noUsers: "No users have left a footprint yet.",
    noGroups: "No groups are registered yet.",
    userMeta: "{{documents}}d / {{conversations}}c",
    groupMeta: "{{documents}}d",
    documentCount_one: "{{count}} document",
    documentCount_other: "{{count}} documents",
    conversationCount_one: "{{count}} conversation",
    conversationCount_other: "{{count}} conversations",
    userDanger: {
      title: "Danger Zone (User)",
      description: "Destructive actions scoped to your account. These affect only you.",
      resetLocal: {
        label: "Reset Local Settings",
        description:
          "Discard your browser-side overrides and fall back to the server-configured defaults. Server-side data is untouched.",
      },
      deleteChats: {
        label: "Delete All Chats",
        description:
          "Permanently delete every chat you own on the server. This action cannot be undone.",
      },
      deleteDocuments: {
        label: "Delete All Documents",
        description:
          "Permanently delete every document, chunk, original, and search index entry you own. This action cannot be undone.",
      },
      resetEverything: {
        label: "Reset Everything",
        description:
          "Permanently delete every server-side trace of your account (conversations, documents, tokens, memory) and clear all local browser data. This action cannot be undone.",
      },
    },
    maintenance: {
      title: "Maintenance (Admin)",
      label: "Maintenance mode",
      description:
        "Lock out every non-admin user and show them a maintenance notice instead of the app. Admins keep full access. The setting is persisted and stays active across server restarts until an admin turns it off.",
      toggleFailed: "Failed to toggle maintenance mode",
      enabled: "Maintenance mode enabled",
      disabled: "Maintenance mode disabled",
    },
    impersonation: {
      title: "Impersonation (Admin)",
      description:
        "Browse the app as another user to reproduce reported issues. The session carries the privileges of the target user, never your admin powers, and a banner with an exit button stays visible at the top.",
      list: "Impersonate a user",
    },
    adminDanger: {
      title: "Danger Zone (Admin)",
      description: "Destructive actions scoped to the whole deployment. These affect every user.",
      resetWorkspace: {
        label: "Reset Workspace Files",
        confirm: "Reset Workspace",
        description:
          "Wipe every workspace file on disk and the matching document rows in SQL, since the two must stay in sync. Chunks (text and vector) cascade with the document rows. Conversations, tokens, memory, users, and groups are kept.",
      },
      reindex: {
        label: "Reindex Knowledge",
        confirm: "Reindex",
        description:
          "Reconcile every casebase: ingest files copied into the workspace by hand, prune SQL orphans, and rewrite filenames to one canonical Unicode spelling so the assistant can address them. Nothing is deleted or re-embedded. Safe to run anytime, useful after manual file changes or an embedding configuration change.",
      },
      resetDatabase: {
        label: "Reset Database",
        description:
          "Drop every user and group row along with everything that cascades: tokens, memory, conversations, documents, and chunks. Workspace files on disk survive.",
      },
      factoryReset: {
        label: "Factory Reset",
        description:
          "Wipe every workspace file on disk, every user and group, and every dependent row (documents, chunks, conversations, tokens, memory). Local browser data is cleared too. The deployment returns to the state of a fresh checkout. This action cannot be undone.",
      },
      wipeUser: {
        list: "Wipe one user's data",
        title: "Wipe data for {{user}}",
        description:
          "Delete every document, chunk, original, conversation, token, and memory entry owned by user {{user}}. {{documents}} and {{conversations}} will be removed. This action cannot be undone.",
        confirm: "Wipe User",
      },
      wipeGroup: {
        list: "Wipe one group's data",
        title: "Wipe data for group {{group}}",
        description_one:
          "Delete every document, chunk, and original owned by group {{group}}. {{count}} document will be removed. The group reappears the next time one of its members (per the OIDC token) uploads to it. This action cannot be undone.",
        description_other:
          "Delete every document, chunk, and original owned by group {{group}}. {{count}} documents will be removed. The group reappears the next time one of its members (per the OIDC token) uploads to it. This action cannot be undone.",
        confirm: "Wipe Group",
      },
    },
  },
} as const;
