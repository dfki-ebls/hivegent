# Usage

## Signing in

Choose Sign In and log in with your organization's account.
The top bar then shows your name and a menu for settings and sign out.

## Chatting with your documents

Type a question and press Enter.
A new chat offers three starters, which have the assistant look through your documents or recent conversations first and then ask you what to do next.
The answer streams in, and the documents the assistant used appear in the context panel.
The agent may take several steps before answering.
Messages you send meanwhile are queued as the next turn, which lets you steer a long answer.

### Good versus weak questions

Ask specific questions about concrete topics.

- Good: "What does our privacy policy say about cookies?"
- Weak: "Show me everything" (too broad) or "Info" (too vague).

## Sources and context

The context panel shows the documents and evidence behind each answer.
Click one to see exactly where the information came from.

The assistant always searches your documents and reports what they say without embellishing.
If your material does not cover a question, it says so.

## Working with documents

### Uploading

- Drag files, folders, or a ZIP archive onto the upload area or onto a folder in the tree, or use the file dialog.
- Uploads and new documents land in the folder you last selected in the tree.
- Hivegent converts each file to text, splits it into chunks, and indexes it.
- The job tray in the top bar shows progress.

Supported formats include PDFs, office documents, web pages, e-books, images, and plain text or Markdown, depending on your deployment.
You can also create and edit Markdown documents in the browser.

### Workspaces and access

- Your Documents is your private workspace.
- Group workspaces are shared with a group, with read or write access depending on your membership.
- Organisation is the workspace all users share, which everyone reads and administrators edit.

### Managing documents

You can view, download, move, rename, or delete each document.
All workspaces share one tree.

- Click a folder to open or close it and a document to view it, and tick the checkbox or hold Ctrl (Cmd on macOS) or Shift while clicking to select entries.
- Drag the selection onto a folder to move it there, also into another workspace.
- Press F2 or click the pencil to rename an entry in place.
- Use the arrow keys to navigate, and Ctrl+Shift+D to move the selection with the keyboard.

A document whose original the browser can display, such as a PDF, a Word document, or a spreadsheet, has a Preview tab with the original next to the Text tab with the converted text.
An image or a video shows above its description instead.
A passage the assistant read opens on the Text tab, highlighted in the document.
You can also include or exclude documents from the current conversation.

### Similar cases

A table, a JSON file, a folder of JSON files, or BPMN process models form a case base: ask the assistant for the records or processes most similar to an example, and it ranks them and explains each match, attribute by attribute, or node by node for processes.
The Similarity tab of such a document shows how each attribute is compared, who decided it (inferred from the data, the AI, or you), and why.
Change a measure or a weight there and save it, try the configuration with a test query, or let the AI refine the decisions nobody took, from statistics and a few values of each attribute.
Until you save it, the configuration is inferred from the data and nothing is written, and Reset to inferred deletes a saved one.
The configuration is stored next to the data, moves and is deleted with it, and is shared with everyone who reads the data.
Records are keyed by their row unless you choose a key column under Key, whose values must be present and unique, and then new cases get the next key in it.
The document tree marks the files and folders with a saved configuration, and the similarity of any folder holding JSON or BPMN files, also in subfolders, opens from its row.
The assistant can also store accepted solutions in a case base and clean it up, as the configuration's `retain` and `maintain` sections allow, which you switch on and adjust in the Similarity tab, giving the reason for every case it adds, changes, or removes.
In Interactive mode it asks for your approval before any file changes, in Write mode it changes the case base directly, and in Read mode it cannot change your workspaces.

## Conversations

The sidebar lists past conversations.

- Titles are generated automatically and can be edited.
- Open, delete, or start conversations at any time.
- Edit an earlier message or regenerate a reply to try a different direction.
- Export a conversation to a file and import it later.
- Transfer a reply to RAGold as a gold benchmark entry, when your deployment offers it.
  The dialog starts from the question, the reply, and the lines it cites, and lets you edit them, grade them as passages, and add documents and further passages, several from one document at a time.
  Nothing is preselected, so you pick the question type, the answerability, and every grade yourself, and mark key facts by selecting words in the reply.
  Once everything is filled in, RAGold opens in a new tab with the entry loaded.
  Allow pop-ups for Hivegent if the browser blocks that tab.

Use Compact to summarize a long conversation so it fits the model's context.
If the model hits its context limit, the conversation is compacted and the reply continues on its own.
A divider marks where the summary starts, and opens to show it.

## Tuning the assistant

- Model: when the operator offers several, pick one in the composer.
  Image attachments and the reasoning effort appear only for a model that supports them.
- Reasoning effort: Low, Medium (the default), or High, trading depth against speed and cost.
- Mode: Interactive asks before each workspace change, Write applies changes to every workspace you may write without asking, and Read allows no changes.
  Changes to skills and memory always ask, in every mode, since they shape later conversations.
- Assistant: when assistants exist, pick one in the composer (see below).

## Memory

The assistant can remember facts across conversations.
It asks before every change to its memory, in every mode, since whatever it saves there reaches all later conversations.
Clear all memory at any time under Settings, Account.

## Skills

Skills teach the assistant a task, such as a house style or a checklist, as a folder with a `SKILL.md` in the [Agent Skills](https://agentskills.io) format.
The assistant sees the name and description of every skill you can use and loads one when a task matches it.
Start a message with `/<name>` to have the assistant load that skill first, the composer suggests matching skills as you type.
Skills of different scopes may share a name, so such a skill is invoked by its reference, such as `/~/<name>`, `/@<group>/<name>`, or `/srv/<name>`, which the suggestions insert.
The text after the command fills every `$ARGUMENTS` in the skill's instructions, so `/review src/app.py` hands `src/app.py` to a skill that reviews `$ARGUMENTS`.
A skill's frontmatter may hold `disable-model-invocation: true`, so only you and assistants load it, never the assistant on its own, or `user-invocable: false`, so it is not offered as a command and the assistant loads it when a task matches.
Every frontmatter value is read as written, so `version: 1.0` stays text, and a key given twice makes the skill invalid.
Settings, Skills lists the organisation's skills, your groups', and your own, opens each one's `SKILL.md`, and shows why a skill cannot be used.
Upload a skill folder or its ZIP archive to your own skills or to a group you may write, where it replaces a skill of the same name, and delete it there.
The assistant may also write a skill when you ask, and asks before every change, in every mode.
Scripts in a skill run in the Python sandbox when they define a `main` function, others stay for reading.
A skill the assistant loaded stays loaded when a long conversation is summarized.

## Assistants

An assistant is a named set of instructions for a recurring kind of work, such as answering from the HR handbook.
It may also load skills, switch tools off, lower the highest mode, and focus on or hide documents, but it never allows more than you could without it.
It may also name the only documents and folders a chat with it reaches, which is enforced, unlike your own selection in the file browser.
Pick one in the composer, which shows the selector only when assistants exist.
Settings, Assistants lists the organisation's assistants, your groups', and your own, and creates, edits, and deletes your own and those of groups you may write.
The selector names each assistant by its scope and name, such as `~/legal` or `@team/legal`, so one of a group never stands in for your own.
The organisation's assistants speak with the operator's authority, while the instructions of your own or a group's reach the assistant as quoted content that cannot override its rules.

## Beyond the web interface

- MCP: if the operator enables it, MCP clients such as editors connect to `/mcp` with the same login.
  They can search, read, explore, and change documents, and every change is shown for confirmation first.
- API: scripts upload, list, download, or delete documents through the REST API or the MCP endpoint, as [Scripting](setup.md#scripting) shows.
  The `hivegent` command-line tool no longer logs in or manages documents, it only administers the server on its host.
