/** Words shared across features. Feature-specific text belongs in its own section. */
export const common = {
  actions: {
    cancel: "Cancel",
    copy: "Copy",
    create: "Create",
    delete: "Delete",
    edit: "Edit",
    open: "Open",
    remove: "Remove",
    rename: "Rename",
    retry: "Retry",
    save: "Save",
    upload: "Upload",
  },
  states: {
    loading: "Loading …",
    loadingImage: "Loading image …",
    working: "Working …",
    error: "Error",
    unknownError: "Something went wrong.",
  },
} as const;
