"""Isolated macOS Vision OCR helper; the caller enforces a process timeout."""
import json
import re
import sys

import Foundation
import Vision


def recognize(data: bytes) -> list[dict]:
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["en-US"])
    request.setUsesLanguageCorrection_(False)
    request.setUsesCPUOnly_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        Foundation.NSData.dataWithBytes_length_(data, len(data)), {},
    )
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(str(error))
    labels = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)[0]
        text = str(candidate.string())
        words = list(re.finditer(r"\S+", text))
        for i, first in enumerate(words):
            for last in words[i:i + 7]:
                start, end = first.start(), last.end()
                offset = len(text[:start].encode("utf-16-le")) // 2
                length = len(text[start:end].encode("utf-16-le")) // 2
                box, _ = candidate.boundingBoxForRange_error_((offset, length), None)
                if box is None:
                    continue
                rect = box.boundingBox()
                labels.append({"text": text[start:end], "x": rect.origin.x,
                               "y": 1 - rect.origin.y - rect.size.height,
                               "w": rect.size.width, "h": rect.size.height,
                               "confidence": float(candidate.confidence()) * 100})
    return labels


if __name__ == "__main__":
    print(json.dumps(recognize(sys.stdin.buffer.read())))
