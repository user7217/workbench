# Sampling & Reconstruction

A bandlimited continuous-time signal $x(t)$ with maximum frequency $f_{\max}$ can be reconstructed exactly from uniform samples taken at $f_s > 2 f_{\max}$.

## Core Principles

- Sampling in time corresponds to periodic replication of the spectrum in frequency.
- Ideal reconstruction applies a rectangular low-pass filter (sinc interpolation in the time domain).
- Practical systems combine an analog anti-aliasing filter with oversampling and digital decimation.
