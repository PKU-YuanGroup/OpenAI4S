# ESMFold2 References

Two files the main recipe pulls in only when a task reaches one of the more specialized model surfaces. Both describe upstream APIs, and what is actually there depends on the version installed.

## Files

| File | Responsibility |
| --- | --- |
| [`design-hook.md`](design-hook.md) | The gradient hook the ESMFold2-Experimental variants expose for design, and why the fused kernel backend must not be turned on for them. This is explicitly experimental guidance, not a promise that the API stays put. |
| [`esmc.md`](esmc.md) | How to load ESMC. Two of its four surfaces come with runnable code annotated with the tensor shapes you get back: MLM logits plus hidden states, and sparse-autoencoder features at layer 60. The other two are prose guidance with no code and no shapes. Zero-shot mutation scoring describes the paper's Alg 14; contact prediction gives P@L-LR benchmark numbers and points at the `esm.models.esmc` module for the regression head. |
