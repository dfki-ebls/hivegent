from dyn.commons.markdown import is_textpassage_incomplete

from .model import Document, TextNode


def premerge_inplace(doc: Document):
    """Merge incomplete text passages around floats.

    Args:
        doc (Document): The document object

    Returns:
        None
    """
    tree = doc.tree
    for _, contentlist in tree:
        i = 0
        last_incomplete_text_idx = -1
        encountered_float = False
        while i < len(contentlist):
            node = contentlist[i]
            if not isinstance(node.data, TextNode):
                encountered_float = True
                i += 1
                continue
            if (
                last_incomplete_text_idx > -1
                and encountered_float
                and is_textpassage_incomplete(node.data.content)
            ):
                contentlist[last_incomplete_text_idx].append(node)
                # print("appended")
                encountered_float = False
                last_incomplete_text_idx = -1
                _ = contentlist.pop(i)
                continue  # do not increment because list got smaller
            else:
                if is_textpassage_incomplete(node.data.content):
                    last_incomplete_text_idx = i
            i += 1
