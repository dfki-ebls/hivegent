import os

import spacy
import wandb
from fastcoref import CorefTrainer, TrainingArgs

from .latex_tokenizer import create_tokenizer

nlp = spacy.blank("en")
nlp.tokenizer = create_tokenizer(nlp)


PROJECT = "math_coref"
RUN_NAME = "fcoref-base"

os.environ["WANDB_PROJECT"] = PROJECT
os.environ["WANDB_RUN_NAME"] = RUN_NAME

wandb.init(project=PROJECT, name=RUN_NAME)


args = TrainingArgs(
    output_dir=f"{PROJECT}-{RUN_NAME}",
    overwrite_output_dir=True,
    # Use the pretrained FCoref model from HuggingFace Hub as the base
    model_name_or_path="biu-nlp/f-coref",
    device="cuda:0",
    epochs=1500,
    logging_steps=100,
    eval_steps=100,
)

trainer = CorefTrainer(
    args=args,
    train_file="coref_data/train_clusters_validated/training_pydantic_strict.jsonl",
    dev_file="coref_data/train_clusters_validated/test_pydantic_strict.jsonl",
    nlp=nlp,
)

trainer.train()
trainer.evaluate(test=True)
wandb.finish()
