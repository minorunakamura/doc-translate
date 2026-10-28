# doc-translate

英語のPDF・EPUBを日本語のMarkdownに翻訳するCLIです。原文、画像、翻訳結果を作業フォルダに保存し、中断後も続きから再開できます。

## 必要なもの

- macOS（動作確認済み：Apple Silicon）。PDFの画像中の文字を読むOCRには、macOSのApple Vision frameworkを使います。
- [uv](https://docs.astral.sh/uv/)とPython 3.14以上
- Node.js 24.21.0以上とnpm（以下の手順ではpnpmは不要です）
- 利用するAIサービスの認証

Linux／Windowsでの動作は未確認です。

## インストール

まだPyPIやreleaseでの配布はしていません。ソースを取得し、PDFとEPUBの両方を使える構成でインストールします。

```sh
git clone https://github.com/minorunakamura/doc-translate.git
cd doc-translate
npm --prefix ai ci --ignore-scripts
npm --prefix ai run build
uv tool install --with-executables-from huggingface-hub '.[pdf]'
```

最後のコマンドで、アプリ・PDF用ライブラリ・ファイルのダウンロードに使う`hf`コマンドをまとめてインストールします。

以後は、どのディレクトリからでも実行できます。

```sh
doc-translate --help
```

コマンドが見つからない場合は`uv tool update-shell`を実行し、ターミナルを開き直してください。

## 初回セットアップ

AIとの通信に使うライブラリを導入し、ログインします。通常は初回だけの操作です。
以下はChatGPTサブスクリプションのOAuthを使う例です。

```sh
doc-translate setup-ai --package-manager npm
doc-translate login --provider openai --method oauth
```

ログイン時に表示される案内に従ってブラウザで認証してください。
ほかのAIサービスやAPI keyを使う場合は[認証の説明](docs/reference/usage.md#providerの認証と削除)を参照してください。料金・利用枠・利用できるmodelは、サービスや契約によって異なります。

## EPUBを翻訳する

まず、指定できるmodel IDを確認します。

```sh
doc-translate models --provider openai
```

次の`MODEL_ID`を一覧の`MODEL ID`に、`book.epub`を翻訳したいファイルのパスに置き換えて実行します。

```sh
doc-translate run book.epub --workspace work/book \
  --provider openai --model MODEL_ID --thinking off
```

`work/book`は翻訳データを保存する作業フォルダです。**まだ存在しないパス**を指定してください。自動で作成されます。

## PDFを翻訳する

### 1. PDFを読み取るmodelファイルをダウンロードする（初回のみ）

PDFでは、ページ内の本文・図などの配置を見つけるHeronと、表の構造を読み取るTableFormerを使います。**画像中の文字を読むOCRとは別の役割**です。OCRにはmacOSのApple Vision frameworkを使うため、別のOCR modelのダウンロードは不要です。

この2つは翻訳を行うAIとも別のもので、ダウンロード後は手元のMacで使います。利用条件（[Heron：Apache-2.0](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md)／[TableFormer：CDLA-Permissive-2.0](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md)）とダウンロード先のファイルサイズを確認してから、次のブロックを実行してください。

```sh
MODELS="$HOME/.local/share/doc-translate/pdf-models"
mkdir -p "$MODELS"

hf download docling-project/docling-layout-heron \
  --revision 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8 \
  --include config.json preprocessor_config.json model.safetensors \
  --local-dir "$MODELS/docling-project--docling-layout-heron"

hf download docling-project/docling-models \
  --revision fc0f2d45e2218ea24bce5045f58a389aed16dc23 \
  --include 'model_artifacts/tableformer/accurate/tm_config.json' \
  'model_artifacts/tableformer/accurate/tableformer_accurate.safetensors' \
  --local-dir "$MODELS/docling-project--docling-models"
```

保存先は`~/.local/share/doc-translate/pdf-models/`です。長いrevision番号やファイル名は書き換える必要はありません。このフォルダは削除せずに残してください。PDFごとにダウンロードし直す必要はありません。

### 2. まず数ページを翻訳する

読み取り結果をページ画像とAIで照合するため、**画像入力に対応したAI model**を選びます。一覧の`IMAGE INPUT`が`yes`のmodelを使ってください。

```sh
doc-translate models --provider openai
```

次の`book.pdf`を翻訳したいファイルのパスに、`MODEL_ID`を選んだmodel IDに置き換えます。この例では、そのmodelを読取り結果の確認と翻訳の両方に使います。

```sh
doc-translate run book.pdf --workspace work/pdf-book \
  --provider openai --model MODEL_ID --thinking off \
  --artifacts-path "$HOME/.local/share/doc-translate/pdf-models" \
  --pages 1-3
```

- `--artifacts-path`：先ほどダウンロードしたmodelファイルの保存先です。
- `--pages 1-3`：PDFの先頭から1〜3ページだけを翻訳します。紙面の印刷ページ番号ではありません。3ページ未満の文書では範囲を変更してください。
- `work/pdf-book`：まだ存在しない作業フォルダを指定します。

全文を翻訳するときは`--pages 1-3`を外し、別の新しい作業フォルダを指定してください。ページ数によって処理時間とAIの利用量が増えるため、短い範囲で結果を確認してから進めることをおすすめします。

## 翻訳結果を見る・再開する

処理が終わると、日本語Markdown・英語Markdown・確認が必要な箇所の記録（QA）の保存先が表示されます。日本語の`.md`ファイルをMarkdown対応のエディタやviewerで開いてください。

出力の`※`は確認が必要な箇所の目印です。詳しい理由はQAファイルで確認できます。**出力完了は、翻訳が正しいという保証ではありません。**

```sh
# 現在の処理状況と出力先を見る（AIへの通信なし）
doc-translate status work/book

# 中断・失敗した処理の続きから再開する
doc-translate resume work/book
```

PDFの例で作った作業フォルダは`work/pdf-book`なので、上のパスを置き換えてください。
`resume`は保存した設定を使って必要な箇所だけを処理します。ただし、送信後に応答を保存できなかった場合などは、再送によって重複課金される可能性があります。

## ほかにできること

| やりたいこと | コマンド／説明 |
| --- | --- |
| AIサービスの一覧を見る | `doc-translate providers` |
| 保存済みデータからMarkdownを作り直す | `doc-translate render work/book`（AIへの通信なし） |
| 翻訳結果をAIに確認させる | [`review`の使い方](docs/reference/usage.md#翻訳結果をaiに確認させる)。選んだ箇所の原文・訳文をAIへ送り、指摘を記録します。自動修正はしません。 |
| 原文や訳文を修正する | [`revise`の使い方](docs/reference/usage.md#原文訳文を修正する推論なし)（上級者向け） |

`providers`／`models`／`run`／`status`の出力をプログラムで読む場合は、`--json`を付けてください。

## 利用前の注意

- 翻訳・AIによる確認では、文書の内容を選んだAIサービスへ送ります。送信する権利や機密性を確認してください。API利用料やサブスクリプションの利用枠を消費する場合があります。modelファイルのダウンロードだけでは、文書は送信されません。
- PDFは文字付き・画像のみの文書、EPUBはDRM解除不要のテキスト中心の文書が対象です。複雑なレイアウトや不鮮明な原本を完全に復元できるとは限りません。
- 出力はMarkdownです。翻訳済みPDF／EPUBの作成や、原本と同じ組版の再現、図の中の文字の翻訳はしません。
- 認証情報・秘密を含む文書やログを共有したり、Issueに添付したりしないでください。

詳しいオプションや動作は[CLI詳細リファレンス](docs/reference/usage.md)、開発・build・検証範囲は[開発ガイド](CONTRIBUTING.md)にまとめています。
