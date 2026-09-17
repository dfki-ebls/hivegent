from pathlib import Path

from hivegent.dyn.pipelines.web import to_chunks

# from hivegent.dyn.segmentation.complex import full_chunking, to_chunks
from hivegent.dyn.serialization.complex.markdown import parse

# print(to_chunks("https://agilemanifesto.org/"))
print(to_chunks("https://bartzinfo.de", False))

# doc = parse(
#     Path(
#         "/home/kilianb/dyn/experiments/coref_data/test/232_GraspNet-1Billion_ A Large-Scale Benchmark for General Object Grasping/hybrid_auto/232_GraspNet-1Billion_ A Large-Scale Benchmark for General Object Grasping.md"
#     )
# )
# full_chunking(doc, 2048)
# print(doc.markdown)
# print("***")
# for c in to_chunks(doc):
#     print(c + "\n---\n")
