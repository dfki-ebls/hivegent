"""LLM-GENERATED. Converts annotated LLM-Annotations and manually annotated data
into fastcoref's training data format.
"""

import json
import os
import random
from glob import glob
from typing import TypedDict, cast

import jsonlines

COMPARISON_DIR = "../train_clusters"
TRAIN_TEST_SPLIT = 0.9

# A mention is a (start, end) character offset span.
Mention = tuple[int, int]
# A cluster is a list of mentions that co-refer.
ClusterMentions = list[Mention]
ClusterMentionsList = list[ClusterMentions]

# Raw, JSON-deserialized shapes (mentions are still plain lists of ints).
RawMention = list[int]
RawCluster = list[RawMention]
RawClusterList = list[RawCluster]


class TextClusterPair(TypedDict):
    text: str
    clusters: RawClusterList


manual_text_cluster_pairs: list[TextClusterPair] = []
llm_text_cluster_pairs: list[TextClusterPair] = []
manually_annotated_files: dict[str, ClusterMentionsList] = {}
llm_annotated_files: dict[str, ClusterMentionsList] = {}


class Cluster:
    id: int
    mentions: ClusterMentions

    def __init__(self, id: int, mentions: ClusterMentions):
        self.id = id
        self.mentions = mentions


def convert_format(objlist: list[TextClusterPair]) -> ClusterMentionsList:
    """Flatten the clusters of a whole annotation file into a single list.

    Document boundaries are dropped: the clusters of every text/clusters pair
    are concatenated, with their mentions converted to tuples so they can be
    used as dictionary keys in the reverse indices.

    Args:
        objlist (list[TextClusterPair]): The deserialized contents of one
            annotation file, one entry per annotated document

    Returns:
        ClusterMentionsList: All clusters of all documents, each mention a
            (start, end) tuple
    """
    result: ClusterMentionsList = []
    for obj in objlist:
        result.extend(convert_tuples(obj["clusters"]))
    return result


def convert_tuples(clusterlist: RawClusterList) -> ClusterMentionsList:
    """Convert JSON mention lists into hashable (start, end) tuples.

    JSON deserializes a mention as a two-element list, which cannot be used as
    a dictionary key; the comparison logic looks mentions up by exact span, so
    they have to be tuples.

    Args:
        clusterlist (RawClusterList): Clusters whose mentions are plain lists
            of two character offsets

    Returns:
        ClusterMentionsList: The same clusters with each mention as a
            (start, end) tuple

    Raises:
        AssertionError: If a mention does not consist of exactly two offsets
    """
    return_list: ClusterMentionsList = []
    for cluster in clusterlist:
        mentions_tuples: ClusterMentions = []
        for mention in cluster:
            assert len(mention) == 2
            start, end = mention
            mentions_tuples.append((start, end))
        return_list.append(mentions_tuples)
    return return_list


for file in glob("*.json"):
    with open(file, "r") as f:
        loaded = cast(list[TextClusterPair], json.load(f))
        manual_text_cluster_pairs += loaded
        basename = os.path.basename(file)
        manually_annotated_files[basename] = convert_format(loaded)

ignored_files = set(glob(f"{COMPARISON_DIR}/*_llm_results.json"))
compared_files = set(glob(f"{COMPARISON_DIR}/*.json"))
compared_files = compared_files.difference(ignored_files)
for file in list(compared_files):
    with open(file, "r") as f:
        loaded = cast(list[TextClusterPair], json.load(f))
        llm_text_cluster_pairs += loaded
        basename = os.path.basename(file)
        llm_annotated_files[basename] = convert_format(loaded)


# Calculate statistics
# Comparison between llm and manually annotated
added_clusters = 0
removed_clusters = 0
matching_clusters = 0

added_mentions = 0  # only in case the cluster itself matched
removed_mentions = 0  # only in case the cluster itself matched
matching_mentions = 0

# stats of the manually annotated clusters
sum_clusters = 0
total_mentions = 0

llm_reverse_indices: dict[str, dict[Mention, Cluster]] = {}
manual_reverse_indices: dict[str, dict[Mention, Cluster]] = {}


# build reverse indices
for filename, llm_clusters in llm_annotated_files.items():
    llm_reverse_index: dict[Mention, Cluster] = {}
    for i, mentionslist in enumerate(llm_clusters):
        cluster_obj = Cluster(i, mentionslist)
        for mention in mentionslist:
            llm_reverse_index[mention] = cluster_obj
    llm_reverse_indices[filename] = llm_reverse_index

for filename, manual_clusters in manually_annotated_files.items():
    manual_reverse_index: dict[Mention, Cluster] = {}
    for i, mentionslist in enumerate(manual_clusters):
        cluster_obj = Cluster(i, mentionslist)
        for mention in mentionslist:
            manual_reverse_index[mention] = cluster_obj
    manual_reverse_indices[filename] = manual_reverse_index


# generate removed clusters, mentions
for filename, llm_clusters in llm_annotated_files.items():
    for cluster in llm_clusters:
        cluster_existed = False
        local_removed = 0
        for mention in cluster:
            if mention in manual_reverse_indices[filename]:
                cluster_existed = True
            else:
                local_removed += 1
        if not cluster_existed:
            removed_clusters += 1
        else:
            removed_mentions += local_removed

# generate matching clusters, mentions + added clusters mentions
for filename, manual_clusters in manually_annotated_files.items():
    for cluster in manual_clusters:
        sum_clusters += 1
        cluster_existed = False
        local_added = 0
        for mention in cluster:
            total_mentions += 1
            if mention in llm_reverse_indices[filename]:
                matching_mentions += 1
                cluster_existed = True
            else:
                local_added += 1
        if cluster_existed:
            added_mentions += local_added
            matching_clusters += 1
        else:
            added_clusters += 1

print("added clusters: ", added_clusters)
print("matching clusters: ", matching_clusters)
print("removed clusters: ", removed_clusters)
print("added mentions: ", added_mentions)
print("matching mentions: ", matching_mentions)
print("removed mentions: ", removed_mentions)
print("---")
print("total clusters: ", sum_clusters)
print("total mentions: ", total_mentions)


# Write train and test files

split_at = round(len(manual_text_cluster_pairs) * TRAIN_TEST_SPLIT)
random.shuffle(manual_text_cluster_pairs)

with jsonlines.open("training.jsonl", mode="w") as writer:
    _ = writer.write_all(manual_text_cluster_pairs[:split_at])
with jsonlines.open("test.jsonl", mode="w") as writer:
    _ = writer.write_all(manual_text_cluster_pairs[split_at:])
print("successfully wrote training.jsonl and test.jsonl")


def resolve_cluster_text(text: str, cluster: RawCluster) -> list[str]:
    """Converts a mentions cluster into the underlying strings.

    Args:
        text (str): the source text
        cluster (RawCluster): the cluster indices

    Returns:
        list[str]: A list of extracted text segments
    """
    texts: list[str] = []
    for start, end in cluster:
        texts.append(text[start:end])
    return texts


# export text clusters for further statistics
llm_clusters_text: list[list[str]] = []
for pair in llm_text_cluster_pairs:
    text = pair["text"]
    cluster_texts = [
        resolve_cluster_text(text, cluster) for cluster in pair["clusters"]
    ]
    llm_clusters_text += cluster_texts
with open("stats/llm_clusters.json", "w") as f:
    json.dump(llm_clusters_text, f)

manual_clusters_text: list[list[str]] = []
for pair in manual_text_cluster_pairs:
    text = pair["text"]
    cluster_texts = [
        resolve_cluster_text(text, cluster) for cluster in pair["clusters"]
    ]
    manual_clusters_text += cluster_texts
with open("stats/manual_clusters.json", "w") as f:
    json.dump(manual_clusters_text, f)
