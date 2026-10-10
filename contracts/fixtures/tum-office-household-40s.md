# Real room video fixture

`tum-office-household-40s.mp4` is a 40-second, 640 × 480 RGB excerpt of a continuous walkthrough of a furnished office/household area. It is derived from the `freiburg3_long_office_household` RGB AVI in the [TUM RGB-D SLAM benchmark](https://cvg.cit.tum.de/data/datasets/rgbd-dataset/download). Dataset credit: J. Sturm, N. Engelhard, F. Endres, W. Burgard, and D. Cremers, “A Benchmark for the Evaluation of RGB-D SLAM Systems,” IROS 2012. The dataset is [CC BY 4.0](https://cvg.cit.tum.de/data/datasets/rgbd-dataset).

Only the first 40 seconds of the RGB movie are included. They were transcoded from the publisher's MPEG-4 AVI to H.264 MP4 for the Mode 2 upload control, without cropping:

```sh
ffmpeg -t 40 -i rgbd_dataset_freiburg3_long_office_household-rgb.avi -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p -movflags +faststart -an tum-office-household-40s.mp4
```

Use this to check video ingestion and the upload-to-mesh workflow. This excerpt shows desks, furniture, partition walls, and portions of a room, but does not cover every wall, the ceiling, or unseen areas. A successful mesh is not proof of room completeness or dimensional accuracy. The original dataset provides depth and camera ground truth for later quantitative evaluation; this fixture includes RGB only.

Local smoke test on the RTX 4060 Laptop GPU: preparation accepted 40 views; a 12-view reconstruction produced a structurally valid GLB with 1,705,993 triangles in 25.9 seconds from upload to export. This is a pipeline check, not a visual-quality or metric-accuracy score.
