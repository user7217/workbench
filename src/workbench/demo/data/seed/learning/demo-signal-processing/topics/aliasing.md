# Aliasing & Folding

When $f_s < 2 f_{\max}$, spectral replicas overlap. Any frequency component above the Nyquist frequency $f_s / 2$ folds back into $[0, f_s / 2]$ and cannot be distinguished from true baseband components after sampling.

- Always place an analog anti-aliasing filter before the ADC.
- In control loops, high-frequency sensor noise aliases into low-frequency drift if unfiltered.
