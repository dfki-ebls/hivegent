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
#
# `version` doubles as the tag to fetch, and is attached with `extendDerivation`
# rather than `overrideAttrs` because `pname` and `version` reach the builder
# and would move the store path over what is only metadata.  The models need
# none of their own: `linkFarm` names their paths in a build script rather than
# their derivations, so the data set is the one describable component.
let
  pname = "tessdata-fast";
  version = "4.1.0";
in
lib.extendDerivation true
  {
    inherit pname version;
    meta = {
      description = "Tesseract language models (tessdata_fast)";
      homepage = "https://github.com/tesseract-ocr/tessdata_fast";
      license = lib.licenses.asl20;
    };
  }
  (
    linkFarm pname (
      lib.mapAttrs'
        (lang: hash: {
          name = "${lang}.traineddata";
          value = fetchurl {
            url = "https://github.com/tesseract-ocr/tessdata_fast/raw/${version}/${lang}.traineddata";
            inherit hash;
          };
        })
        {
          deu = "sha256-GdIZu7ZnLIadIKljbGgWqB65pxeWy5Pr4MsVMOLNsi0=";
          eng = "sha256-fUMivSp3SXJIeWg/w5EstULxmQbIO8waUhMlVkJxcLI=";
          osd = "sha256-nPXVdvzEdWTxEmWEHlyoOQAefm84/396rPRtFalrAP8=";
        }
    )
  )
