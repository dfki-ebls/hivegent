{
  lib,
  fetchurl,
  linkFarm,
}:
# Tesseract language models for the OCR engines linked into the backend
# (tesserocr for docling, kreuzberg) — both resolve this directory via
# TESSDATA_PREFIX at runtime.  The models are the official quantized LSTM
# ones from tesseract-ocr/tessdata_fast (~2x faster recognition than the
# combined tessdata that nixpkgs ships); osd backs the orientation
# detector that docling always instantiates.  Keep the language set in
# sync with what `conversion.ocr_languages` may be configured to.
linkFarm "tessdata-fast" (
  lib.mapAttrs'
    (lang: hash: {
      name = "${lang}.traineddata";
      value = fetchurl {
        url = "https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/${lang}.traineddata";
        inherit hash;
      };
    })
    {
      deu = "sha256-GdIZu7ZnLIadIKljbGgWqB65pxeWy5Pr4MsVMOLNsi0=";
      eng = "sha256-fUMivSp3SXJIeWg/w5EstULxmQbIO8waUhMlVkJxcLI=";
      osd = "sha256-nPXVdvzEdWTxEmWEHlyoOQAefm84/396rPRtFalrAP8=";
    }
)
