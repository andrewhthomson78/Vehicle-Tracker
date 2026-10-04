// Prints the text in an image, one line per row, using macOS's built-in Vision OCR.
// Usage: ocr <image-path>
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("could not open image\n".data(using: .utf8)!)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["en-GB", "en-US"]

do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
} catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}

// group observations into rows (similar y), then read each row left to right
let obs = (request.results ?? []).compactMap { o -> (CGRect, String)? in
    guard let t = o.topCandidates(1).first?.string else { return nil }
    return (o.boundingBox, t)
}.sorted { $0.0.midY > $1.0.midY }

var rows: [[(CGRect, String)]] = []
for o in obs {
    if let last = rows.last, let first = last.first, abs(first.0.midY - o.0.midY) < first.0.height * 0.6 {
        rows[rows.count - 1].append(o)
    } else {
        rows.append([o])
    }
}
for row in rows {
    print(row.sorted { $0.0.minX < $1.0.minX }.map { $0.1 }.joined(separator: "   "))
}
