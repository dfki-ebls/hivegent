from hivegent.dyn.commons.markdown import build_title
from hivegent.dyn.segmentation.tabular_data import chunked_table
from hivegent.dyn.serialization.complex.markdown.model import (
    Document,
    Node,
    TableMdNode,
    TableNode,
    split_node,
)


def _split_section_tables_inplace(title: str, contentlist: list[Node], limit: int):
    """Split table nodes within a single section in place.

    Args:
        title (str): Title used for splitting tables.
        contentlist (list[Node]): List of nodes to process.
        limit (int): Maximum number of table chunks to create.

    Returns:
        None: None
    """
    idx = 0
    while idx < len(contentlist):
        node = contentlist[idx]
        if isinstance(node.data, (TableMdNode, TableNode)):
            headers, vals = node.data.headers, node.data.vals
            table_chunks = chunked_table(title, headers, vals, limit)
            split_node(contentlist, idx, table_chunks)
            idx += len(table_chunks)
        else:
            idx += 1


def split_tables_inplace(doc: Document, limit: int):
    """Converts table nodes into appropriate chunks.

    Args:
        doc (Document): The document object
        limit (int): The limit for splitting tables

    Returns:
        None
    """
    tree = doc.tree
    doc_title = doc.title
    for heading, contentlist in tree:
        _split_section_tables_inplace(
            build_title(doc_title, heading), contentlist, limit
        )
