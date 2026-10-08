# CC0 video input fixture

`cc0-sample-60s.mp4` is a public-domain MP4 downloaded on 2026-10-09 from
<https://cdn.truefilesize.com/mp4/sample-30mb.mp4>. The publisher labels the
asset CC0/Public Domain and publishes this SHA-256:

```text
2e4a094ad414a14e2ae5f62a0cfbd8597cf6a5c6ee1212211e5072d7f999f2b9
```

Local verification: 31,457,280 bytes; H.264/AAC; 1280×720; 30 fps; 60.0 seconds.
The remote filename is `sample-30mb.mp4`; it is not a 30-second clip.

This file verifies video upload, FFmpeg frame selection, source traceability, and
the imagery reconstruction flow. It is not ground-truth room data and cannot be
used to claim room coverage or dimensional accuracy.

## Local smoke result

On the local RTX 4060 Laptop GPU, 2026-10-09: preparation accepted 35 source
frames and reconstruction selected 12 uniformly spaced views. The complete
accepted-input-to-saved-GLB run took 23.16 seconds (3.09 seconds preparation,
19.86 seconds worker/validation), produced 17,059 vertices and 33,458 colored
triangles, and peaked at 4,639,078,400 allocated GPU bytes. The exported GLB
passed Khronos validation with zero errors or warnings.
