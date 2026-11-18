This experiment does not send this file to the model.

The one-shot agent in tools/one_shot.py sends only the reconstructed Colab
CodeGen prompt, the text between the VeriContest prompt markers in each
instruction.md, as one user message. This keeps the run comparable to the Colab
pilot. Harbor appends this file to each instruction, and the agent drops it with
the other text outside the markers. Each request.json records
"system_prompt_sent": false.
