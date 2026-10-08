# Mode 2: capture ingestion (Milestone 1)

In Reconstruct, select **Mode 2 · Photos & video**. Upload a 30–60 second MP4/MOV
walkthrough, or 20–40 PNG/JPEG photos in walking order. The file list shows the
order that will be processed; use the arrow buttons to correct photo order. Cover one static room in good lighting, maintain
roughly 70% overlap, and include textured corners, furniture, floor and ceiling.
Keep people still; avoid mirrors, digital zoom, blur and abrupt turns.

The API needs `ffmpeg` and `ffprobe` on PATH for video; photo ingestion needs only
the existing Python dependencies. Check with `ffmpeg -version` and `ffprobe -version`.
No GPU/model dependencies are loaded. Preparation runs serially on the existing
background job thread. This milestone ends at an accepted reconstruction input;
it does not create a mesh or establish metric scale.

Limits: 512 MB total per capture, 20 MB per photo by default (the API's existing
upload-size setting), 8000 px per photo side, 4096 px per video side. Both require
at least 240 px per side and aspect ratios between 1:4 and 4:1. PNG/JPEG content is
decoded; MP4/MOV needs an ISO media `ftyp` header and a decodable video track.
HEIC, playlists, panoramic images and multi-room captures are not supported.

## Preparation and evidence

Originals retain their bytes, filename, upload order, size and SHA-256. JPEG EXIF
orientation and video display rotation are baked into selected PNGs. Photos are
resized to at most 1280 px; video is fitted into a 1280 px box. Video selection
samples existing frames at least 0.5 seconds apart, without interpolated frames.
`timestampSeconds` is selected presentation time relative to the first frame;
`sourceTimestampSeconds` adds the source video stream start time. Photos reference
their stable original source IDs with a null timestamp.

A fixed-resolution Laplacian/ORB check removes blurred or feature-poor views.
Thumbnail difference removes near duplicates. Ordered temporal bins choose sharp
views across the capture, up to 40, with at least 12 required. ORB matching with
RANSAC fundamental-matrix inliers checks a connected graph among each view's
previous three neighbors. Disconnected selections are rejected with retake advice.
The output includes rejected-view reasons and overlap evidence. Heuristics can
reject valid low-texture captures or accept imperfect coverage: they do not prove
room completeness, geometric accuracy or calibrated confidence. Real capture
threshold tuning remains necessary.

Every job writes `projects/{id}/captures/{jobId}/manifest.json` and `frames/*.png`.
Manifest v1.0.0 is the common worker input for both source types. Paths are relative
to the project root. The HTTP manifest adds project-scoped frame URLs. It records
processing thresholds/version, OpenCV/FFmpeg versions, source hashes and selected
frame hashes. Same inputs and tool versions yield the same selected pixels and
source references; job IDs and paths differ on reruns.

Projects retain the latest successful manifest pointer. Failed/cancelled retries
preserve it. Intermediate candidates are removed; failed preparation directories
are removed. Restarted active jobs are marked failed and unpublished partial
outputs cleaned. Cancellation checks run between processing stages; an active
FFmpeg call can take up to its 90-second timeout to return before cancellation.
Originals and completed job artifacts remain for traceability.

## Validation

From `services/api`: `.venv/bin/python -m pytest tests/test_captures.py -q`.
From `apps/web`: `npm test -- --run tests/captures.test.tsx`, then `npm run build`.
Video tests require FFmpeg and generate a textured 30-second clip locally. Tests
also exercise photo success, EXIF rotation, blank/duplicate/disconnected inputs,
corrupt uploads, cancellation, retries, artifact access and saved metadata.
These synthetic cases validate ingestion mechanics, not reconstruction quality.
