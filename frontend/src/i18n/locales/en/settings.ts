/** The settings pages of every user and the read-only administration pages. */
export const settings = {
  nav: {
    title: "Settings",
    profile: "Profile",
    skills: "Skills",
    assistants: "Assistants",
    account: "Account",
  },
  danger: {
    done: "{{action}}: Done",
    failed: "{{action}}: Failed",
  },
  profile: {
    title: "Profile",
    description: "Who you are signed in as and what your sign-in grants you.",
    name: "Name",
    email: "Email",
    id: "User ID",
    roles: "Roles",
    noRoles: "No roles",
    workspaces: "Workspaces",
    writable: "Read and write",
    readOnly: "Read only",
  },
  skills: {
    title: "Skills",
    description:
      "Instructions and scripts the assistant loads for matching tasks, or when a message starts with /name. Upload a skill folder or its ZIP archive to your own skills or a group's, the organisation's come from the operator.",
    empty: "No skills yet.",
    uploadArchive: "Upload ZIP",
    uploadFolder: "Upload Folder",
    installed: "Installed the skill {{name}}",
    notRunnable_one: "{{count}} script does not run in the sandbox and stays for reference.",
    notRunnable_other: "{{count}} scripts do not run in the sandbox and stay for reference.",
    delete: {
      label: "Delete",
      title: "Delete the skill {{name}}?",
      description: "The skill and all its files are deleted for everyone who sees it.",
    },
  },
  assistants: {
    title: "Assistants",
    description:
      "Named instructions you pick in the chat, with the skills they load and the tools, mode, and documents they narrow the assistant to. The organisation's come from the operator, a group's are shared with its members.",
    empty: "No assistants yet.",
    create: "New Assistant",
    edit: "Edit",
    saved: "Saved the assistant {{name}}",
    delete: {
      label: "Delete",
      title: "Delete the assistant {{name}}?",
      description: "The assistant is deleted for everyone who sees it.",
    },
    form: {
      createTitle: "New assistant",
      editTitle: "Edit {{name}}",
      description: "Everything but the instructions only narrows what a chat with it may do.",
      name: "Name",
      nameHint: "Lowercase letters, digits, and hyphens, such as support-team.",
      summary: "Description",
      instructions: "Instructions",
      instructionsHint:
        "Instructions from a group or your own reach the assistant as quoted content, never as rules that override the operator's.",
      skills: "Skills to load",
      disabledTools: "Tools to switch off",
      maxMode: "Highest mode",
      includedDocuments: "Documents to focus on",
      excludedDocuments: "Documents to hide",
      allowedDocuments: "Only these documents",
      documentsHint: "Paths such as ~/handbook or @team/faq.md.",
      allowedDocumentsHint: "Enforced while the assistant is selected, none for no restriction.",
      directory: "Directory",
      addPath: "Add path",
      removePath: "Remove path",
      save: "Save",
      cancel: "Cancel",
    },
  },
  account: {
    title: "Account",
    description:
      "Export your data, clear what the assistant keeps for you, or delete the data of your account.",
    clear: "Clear",
    export: {
      title: "Export",
      description:
        "Download your data as a ZIP archive, everything at once or one part at a time. The archive is a copy to keep, not one to import again.",
      download: "Download",
      parts: {
        all: {
          label: "Everything",
          description: "Every part below in one archive.",
        },
        documents: {
          label: "Documents",
          description: "The files of your personal workspace, originals and their markdown.",
        },
        conversations: {
          label: "Conversations",
          description: "Every chat with the system prompts it ran under, and its temporary files.",
        },
        memory: {
          label: "Memory",
          description: "What the assistant remembers about you across conversations.",
        },
        skills: {
          label: "Skills",
          description: "The files of your own skills.",
        },
        assistants: {
          label: "Assistants",
          description: "The definitions of your own assistants.",
        },
        preferences: {
          label: "Preferences",
          description: "The choices you made for your chats.",
        },
      },
    },
    clearMemory: {
      label: "Clear Memory",
      description:
        "Permanently delete all saved memory. The assistant will no longer remember information from previous conversations.",
    },
    clearTmp: {
      label: "Clear Temporary Files",
      description:
        "Delete the working files the assistant keeps for each of your conversations. Ongoing conversations lose their parked state, and your documents are untouched.",
      done_one: "Cleared {{count}} temporary file",
      done_other: "Cleared {{count}} temporary files",
    },
    danger: {
      title: "Danger Zone",
      description: "Destructive actions scoped to your account. These affect only you.",
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
          "Permanently delete every server-side trace of your account (conversations, documents, memory, skills, assistants) and clear all local browser data. This action cannot be undone.",
      },
    },
  },
  admin: {
    title: "Administration",
    readOnly:
      "The operator configures all of this on the host, so these pages only show what is in effect.",
    everyone: "Everyone",
    nav: {
      overview: "Overview",
      models: "Models",
      plugins: "Plugins",
      integrations: "Integrations",
      sources: "Sources",
      pipelines: "Pipelines",
      skills: "Skills",
      maintenance: "Maintenance",
    },
    overview: {
      title: "Overview",
      description:
        "The users and groups that stored something here. Membership lives in the identity provider, so a group or user appears once its workspace or conversations hold data.",
      users: "Users",
      groups: "Groups",
      noUsers: "No user has stored anything yet.",
      noGroups: "No group has stored anything yet.",
      documents_one: "{{count}} document",
      documents_other: "{{count}} documents",
      conversations_one: "{{count}} conversation",
      conversations_other: "{{count}} conversations",
    },
    models: {
      title: "Models",
      description: "The chat models of the operator's catalog, which users pick among.",
      empty: "No chat model is configured.",
      default: "Default",
      vision: "Vision",
      contextWindow: "{{tokens}} tokens of context",
    },
    plugins: {
      title: "Plugins",
      description:
        "The enabled plugins and what each contributes. A tool that only reads runs in every mode, every other one follows the approval rules of its effect.",
      empty: "No plugin is enabled.",
      converters: "Converters",
      chunkers: "Chunkers",
      skills: "Skills",
    },
    integrations: {
      title: "Integrations",
      description:
        "The MCP servers, databases, and APIs configured on the host, whose tools the assistant is offered as <id>_<tool>. Tools that only read run in every mode, every other one asks first.",
      empty: "No integration is configured.",
      mcp: "MCP servers",
      database: "Databases",
      api: "APIs",
      connected: "Connected",
      unreachable: "Unreachable",
      trusted: "Trusted",
      tools_one: "{{count}} tool",
      tools_other: "{{count}} tools",
    },
    sources: {
      title: "Sources",
      description:
        "The external sources configured on the host, mirrored to /mnt/<id> and indexed for search. Sources are read-only, so the assistant and every user only read them.",
      empty: "No source is configured.",
      synced: "Last synced {{when}}",
      never: "Never synced",
      running: "Syncing",
      progress: "Syncing {{done}} of {{total}}",
      waiting: "Sync requested",
      held_one: "{{formatted}} deletion held",
      held_other: "{{formatted}} deletions held",
      confirmDeletes: "Confirm deletions",
      confirmTitle: "Delete the held files of {{id}}?",
      confirmDescription:
        "Deleting files gone from the source was held, since so many look like an outage. Confirm only if they were removed at the source on purpose.",
      items_one: "{{formatted}} file",
      items_other: "{{formatted}} files",
      interval: "Every {{minutes}} min",
      syncNow: "Sync now",
      requested: "Sync of {{id}} requested",
      syncFailed: "Failed to start the sync",
    },
    pipelines: {
      title: "Pipelines",
      description: "The installed conversion and chunking pipelines, which uploads may pick.",
      conversion: "Conversion",
      chunking: "Chunking",
    },
    skills: {
      title: "Skills",
      description:
        "The organisation's skills, from the operator's directory and the enabled plugins, which every user sees.",
    },
    maintenance: {
      title: "Maintenance",
      description: "Operational switches that take effect without a restart.",
      label: "Maintenance mode",
      hint: "Lock out every non-admin user and show them a maintenance notice instead of the app. Admins keep full access. The setting is persisted and stays active across server restarts until an admin turns it off.",
      toggleFailed: "Failed to toggle maintenance mode",
      enabled: "Maintenance mode enabled",
      disabled: "Maintenance mode disabled",
      danger: {
        title: "Index",
        description:
          "Actions on the whole index. Wiping another user's or a group's data, or the whole deployment's, runs on the host with `hivegent reset`.",
      },
      reindex: {
        label: "Reindex Knowledge",
        confirm: "Reindex",
        description:
          "Reconcile every workspace: ingest files copied into the workspace by hand, prune SQL orphans, and rewrite filenames to one canonical Unicode spelling so the assistant can address them. Nothing is deleted or re-embedded. Safe to run anytime, useful after manual file changes or an embedding configuration change.",
      },
    },
  },
} as const;
