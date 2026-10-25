# doc-translate — PDF・EPUB日本語翻訳CLI

> 状態：設計議論で合意した基本方針と検証結果の整理。CLI help・保存・通信codecの初期基盤のみ実装済みで、文書処理操作は未実装。設計上の決定は実装・検証済みを意味しない。
>
> 346ページの別書籍は全文初版生成・構造検証・標本レビューまで到達したが、内容品質は未承認。確認済み結果と限界を§18に記録する。本書の更新は実装計画の承認や実装開始を意味しない。

## 1. 目的と対象

技術文書の内容を、原本へ戻って確認できる状態を保ちながら、日本語Markdownへ変換する。

現在の入力対象は、英語の技術PDFと**テキスト主体のEPUB**。EPUB入力は今回の対応要件に含めるが、実装・実データでの検証は未実施である。

- PDF：テキストを持つPDFと画像のみのPDFを同じ基本ワークフローで扱う。実スキャンのノイズ等への耐性は未検証。
- EPUB：本文をXHTMLのtextとして取得できる、DRM解除不要のEPUB 2／3のリフロー型を初期対象とする。挿絵・図・表・コード・脚注・MathMLを含んでいてもよい。画像主体・fixed-layout EPUB、画像内文字のOCR、DRM解除、非整形式XMLの自動修復、DTBook専用本文、複数renditionの自動選択は対象外。対象外入力は理由を示して停止し、黙って飛ばして全文処理完了にしない。

現在の出力対象はMarkdownのみ。翻訳前原文・訳文・構造情報を出力形式から独立して保存し、将来PDF・EPUBの出力生成を追加できる責務分離とする。**EPUB入力への対応と、日本語EPUBの生成は別要件**であり、後者は将来の拡張候補に留める。任意の入力を正確に読めるとは保証しない。

優先すること：

- 本文を要約せず、技術的な意味を保って翻訳する。
- PDFは抽出下書きの誤りを原本画像との照合で補い、EPUBは元のtext・構造を損なわず取得する。
- コード・数式・図・表構造を翻訳処理で壊さない。
- 未読・欠損・不確かな箇所を隠さない。
- 翻訳前原文を再利用し、翻訳や局所修正だけをやり直せる。
- 長時間処理の完了結果・状態・利用量を保存する。

プロジェクト名・CLI名は`doc-translate`とし、主入口は独立したPython／uv CLIとする。推論はNode helperから`@earendil-works/pi-ai@1.4.0`を直接利用し、Pi CLI／coding agent／Pi Skillを利用者に要求しない。package・CLIの具体形と実装順序は[正式Plan](../plans/doc-translate-initial.md)、現在の実装済み契約は[artifact契約](../reference/artifacts.md)を参照する。動作検証基準はmacOS 26.7.1 arm64／Python 3.14／Node 24とし、macOSの最低versionやIntel、Linux／Windowsの実機保証はしない。

用語は[プロジェクト用語集](../../GLOSSARY.md)を参照する。対象書籍の訳語を定める「文書glossary」と、この用語集は別物である。

## 2. 採用する基本構成

```text
PDF → Docling＋ページ画像 → 補正LLM → 検証・ページ間接続 ─┐
                                                       ├─ 翻訳前原文の保存
テキストEPUB → 読み順・XHTML等の構造抽出・検証 ──────────┘
                              ↓
          文書context・文書glossaryの自動準備・保存
          （用語候補・用例・出典位置の収集と整理）
                              ↓
翻訳unit ＋ 文書context ＋ 固定した文書glossary／文体policy
                              ↓
                  翻訳LLM：日本語への翻訳（画像なし）
                              ↓
                  Python：検証・原文との対応付け
                              ↓
        翻訳前原文 ＋ 訳文対応表 ＋ 構造情報・図assets
                 ├─ Markdown生成（現在の対象）
                 ├─ PDF生成（将来追加）
                 └─ EPUB生成（将来追加）
```

**PDFの抽出ツールはDoclingに固定**する。PDF用のPyMuPDF4LLM等との切替機能やMarkerの導入は、現時点の要件に含めない。EPUBは文書構造を直接取得し、Doclingの利用や一度PDFへ変換することを必須にしない。

推論接続はPython → Node helper（LF区切りJSONL）→ pi-ai → 選択providerとする。Pi CLIと同じprovider推論能力を使いながら、CLI導入やcoding agent設定への依存をなくす。CLI固有の画像前処理・設定overlayは自動では引き継がないため、policy・画像・model・thinkingの実効入力を明示検査する。§18のPi実験は直接経路で再実証した結果ではない。

PDFの2段階とは、OCRと翻訳だけの分離ではなく、**原文・構造補正と翻訳の分離**を指す。DoclingのOCRは画像からの文字抽出を担い、補正LLMは抽出結果を原本画像と照合して復元する。テキストEPUBの通常経路ではOCR・画像補正LLMを使用せず、抽出・検査した原文から共通の翻訳工程へ進む。

## 3. 責務

| 担当 | 責務 | 担当しないこと |
| --- | --- | --- |
| Docling（PDF） | 文字・構造・画像・ページ・bbox・refの抽出。必要に応じたOCR | 抽出結果の正確さの認定、日本語翻訳 |
| EPUB構造抽出（Python） | 読み順、XHTML等のtext・構造・参照・assetsの取得と検査 | OCR、画像補正、本文の創作、日本語翻訳 |
| 補正LLM | 原本画像を根拠にした原文・構造の補正、不確実性の報告 | 著者のコード改善、欠損の創作、ファイル操作、処理状態管理 |
| 文書context・glossary準備 | 翻訳前原文から用語候補・用例・出典位置を集め、利用者指定を優先してcontext・採用glossaryを保存 | 原文の再補正、訳文の生成、準備結果だけによる内容品質の認定 |
| 翻訳LLM | 確定した翻訳unitをcontext・glossary・文体policyに沿って翻訳 | 原文の再補正、unitの分割・統合、表構造・コード・数式の変更 |
| Python Workflow | 入出力準備、分割、helper起動、validation、有限retry、接続、保存、文書state・再開・利用量管理、rendering | 不確かな内容を規則だけで推測補完すること |
| Node helper＋pi-ai | provider／model metadata、推論接続、公開認証APIの実行、最小credential保存 | 文書state・snapshot採用、tool実行、探索、agent loop、独自OAuth protocol |

PDFの補正LLMと翻訳LLMは呼出しごとに別Node helper processで動かす。EPUBには、PDFと工程数を揃えるためだけの補正LLMを追加しない。原文取得・検査の成功を、そのまま翻訳結果の品質保証とは扱わない。機械検査は全範囲で行い、追加LLMレビュー・局所修正は必要な範囲に限定する。全文の追加LLMレビューは標準では必須にしない。

## 4. 原資料と正本の扱い

- 原本文書（PDF／EPUB）は不変とし、hashを記録する。
- PDFではDocling JSONを抽出下書きとして保存し、原本画像と照合した補正済み原文を保存する。Doclingの木を正しい構造の正本とはみなさない。
- EPUBでは元のpackage・XHTML等を不変のまま保持し、text・構造・読み順を取得して検査した原文を保存する。補正していない結果を「補正済み」と呼ばない。
- 両経路の出力を、**翻訳前原文のsnapshot**として共通の翻訳入力にする。未確認事項を消して「正しい原文」と断定しない。
- 修正時は旧snapshotと変更理由を保持し、原回答の上書きだけで履歴を失わない。
- 最終的な読み順・階層・表構造は、形式に応じて取得・検証した翻訳前原文を使用する。
- 出力生成の正本は、翻訳前原文・訳文対応表・構造情報・図assetsとする。Markdownを唯一の中間正本にせず、他形式のためにMarkdownを再解析して構造を復元することを前提にしない。閲覧用の英語Markdownも派生成果物である。

## 5. 入力形式別の原文取得

### PDF：Docling抽出・画像準備

Doclingのversion、OCR設定、使用するOCR／layout modelの識別情報、抽出設定、原PDF hashを保存する。raw itemの本文・種別・ref・provenanceと、認識されたtable cell等の構造を捨てない。

実行前にNode helper・provider認証・Docling環境・ページ画像生成手段・入力の読込み可否を確認する。Docling抽出はローカル実行とし、`enable_remote_services=False`、`allow_external_plugins=False`、Hub／Transformersのoffline設定とlocal artifact pathを使う。OCR／layoutのローカルmodelと、pi-aiのLLM providerは別の依存として扱い、不足modelを変換中に自動取得しない。

依存package・model重み・画像処理手段の利用／配布条件はそれぞれ確認し、Doclingの採用だけで全て同じlicense条件だと扱わない。PDF依存はoptional extraに分離し、OCRMacのApple Vision（`en-US`／`vision`／`accurate`）を使用する。固定model・revision・licenseは正式Planによる。Apple VisionのOS条件をMITとみなさず、重みは同梱しない。

Doclingのcharspanには保存textの範囲を超える例があったため、offsetの妥当性を検査する。不一致を黙って切り詰めず、必要なitem全文・位置情報・画像から対象範囲を判断する。

補正には、所有ページすべての原本画像を渡す。抽出が成功したという理由だけで画像を省略する最適化は、現時点の方式に含めない。

PDF内の1始まりのページ番号を追跡の基準とする。印刷されたページ番号・Roman numerals・分割PDF内の番号とは区別する。bboxには座標系・単位・参照画像を伴わせ、Doclingの座標と切出し画像の座標を混同しない。

抽出を分割する場合も元PDFへの対応を維持し、分割ごとのローカルrefを文書全体で一意とみなさない。

### テキストEPUB：本文・構造の直接取得

- packageのmanifest・spine等から本文resourceと標準の読み順を取得する。ファイル名の並びだけで本文順を決めない。
- 本文をXHTMLから取得し、見出し・段落・リスト階層・表cell・caption・脚注・リンク・コード注釈の対応を保持する。目次や補足資料を本文と混同せず、spineの読み順だけを見て参照先の脚注等を見落とさない。
- 原文位置は**EPUB内resource path＋要素IDまたはsnapshot内のnode位置**で追跡する。要素IDがない場合も内部IDとの対応を残し、架空の原本IDや固定ページ番号を作らない。
- 文中の強調・リンク等を保ちながら翻訳unitを作る。各text nodeを機械的に別unitへ切り、文脈や日本語の語順を壊す方式にしない。
- `pre`／`code`、literal、identifier、URLを保護する。コードの空白・改行、表のcell・結合情報、脚注やリンクの参照先を捨てない。タグ・ID・href・CSS等を翻訳LLMへ自由に書き換えさせない。
- 通常の挿絵・図は元assetとして保持する。画像alt等の説明textも対応を保存して翻訳対象にできるが、画像内文字のOCR・翻訳は行わない。数式は元データと構造を保護し、平文へ無理に潰さない。
- 正しく取得できない構造・欠損は元resourceと要確認事項を残す。対象外の画像主体・fixed-layout EPUBを、無断でOCRや画像補正LLMへ切り替えない。読み込めない入力やDRM解除が必要な入力は明示して停止する。
- 展開時のパス逸脱・過大な展開サイズ、XMLの外部参照等を検証し、script・外部resourceの実行／自動取得を禁止する。出力するmarkup・assetsにも、未承認の実行可能な内容や外部参照を無条件に引き継がない。

受理範囲は§1、MathML等の出力方針は§16による。使用parser、細部の抽出・対象判定規則、安全なmarkup生成の具体的方法は後続で具体化する。上記は対応要件であり、実装・実データによる成立性は未検証。

## 6. 補正LLMの契約（PDF）

入力は、所有ページ・参照ページの指定、抽出下書き、対応する原本画像、補正policy。出力は構造化JSONとする。原文は原語で返し、この段階で日本語訳を生成しない。補正理由・要確認事項の説明は日本語でよい。

### 復元・保持する内容

| 内容 | 必要な表現と制約 |
| --- | --- |
| 本文・見出し | 原文、種別、読み順、章節の階層。calloutの見出しと章節見出しを区別する |
| リスト | 各itemの原文、深さ・親子関係、必要なmarker情報。インデントだけを後から推測することに依存しない |
| コード | 実際のlanguage、可視のtoken・literal・改行・インデント。C#専用にしない |
| コード注釈 | 印刷上の番号・説明・対象行の対応を、program tokenとは別に保持する |
| 数式 | 数学的意味と英語ラベルを保った表現。LaTeXへ転記する場合も内容を変えない |
| 表 | 論理的な行・列・cell・空cell・見出し。折返しを別行にしない |
| 図 | 原本画像から保持する領域、元ページ、captionとの対応。図内文字を本文として二重出力しない |
| caption・脚注 | 原文と対象との関係。本文への混入や脱落を避ける |

全blockから元ページと根拠refを追跡できるようにする。1itemの分割・複数itemの統合を認める。画像にしか存在しない内容はrefなしでもよいが、元ページ・見える位置等の根拠と復元理由を記録し、架空のrefを作らない。

図については、補正LLMが**図全体の保持範囲と、その内部に属する抽出内容の対応**を示す。Pythonはこの対応を使い、図を原画像で保持し、図内文字を本文・表cell・翻訳unitとして二重出力しない。captionと図の外側の説明文は別に扱って翻訳する。近接やbboxの重なりだけで本文を除外せず、判別できない箇所は原本画像と要確認事項を残す。この対応付けによる誤分類防止の効果は未実証。

除外するrunning header／footer／navigation等は、除外内容と理由を残す。本来の本文・脚注・captionを除外してはいけない。

### 補正しないもの

- 著者の文章やコードの改善、意味の書換え。
- 見えない文字、閉じ括弧、欠けたコード、未提供ページの続きを想像で埋めること。
- コードの正規化を理由にliteral・URL・identifierを変更すること。
- 数字を一律に注釈とみなして削除すること。コード注釈と数値literalは画像上の根拠で区別する。

原本自体の欠損と、処理中に起きた誤りを分ける。読めない箇所は、読める範囲・原本画像・要確認事項を保持する。原本のコードが不完全なら、コンパイル可能に修理しない。

## 7. 所有範囲・ページ間接続（PDF）

各補正処理に重複のない所有ページを割り当てる。隣接ページは接続判断のcontextとして渡し、その内容を重複出力させない。

補正LLMはページごとの可視内容と継続情報を返し、Pythonがそれを照合して接続する。文章の空白結合、ページ境界の分綴の接続、コードの改行結合を区別する。単に内容が関連するという理由で別段落を結合しない。

**文章の接続と、リスト等の親子関係の継続は別に扱う。** 補正LLMは参照ページも使って、所有ページの項目がどの親項目に属するかを示す。Pythonは項目を別の原文blockとして保持したまま親子関係を組み立て、翻訳contextと出力の階層へ引き継ぐ。親項目を重複出力したり、項目の文章を一つに連結したりしない。親を特定できない場合は要確認にする。

前後の継続情報・種別・ページ順が矛盾した場合は、自動で片方を正しいと決めず要確認にする。文字を読めても接続を確定できない場合は、それだけを理由に本全体を止めず、原文断片と出典を保持して別々に翻訳する。翻訳LLMに続きを創作させず、§16の目印とQAレポートで確認箇所を示す。最終ページで継続が残る場合も確認対象とする。

長いコード・表が範囲に収まらない場合は、contextや分割を調整するか、画像を保持して要確認にする。出力上限を避けるための要約・省略は認めない。複数ページ表の一般的な接続は、まだ検証済みではない。

## 8. 翻訳unitと原文対応

翻訳unitは、PDFの補正・接続後、またはEPUBの構造抽出・検査後の**翻訳前原文**からPythonが生成する。例は段落、見出し、リストitem、caption、脚注、表のtext cell、画像alt等の説明text。コード本体・数式・図は翻訳unitにしない。

最低限保持する情報：

- unit固有ID、原文、種別、所属する原文block。
- 入力形式に応じた原文位置：PDFはページ・抽出item、EPUBはresource path・要素／nodeへの対応。
- 表の行・列や見出しpath等、解釈に必要なcontext。
- 原文と入力contextのhash、参照したsnapshot。

unit IDは入力形式固有の位置情報と別のidentityとする。Docling refやEPUB内の要素IDだけを翻訳対応表の一意keyにしない。1つの原文blockから複数cellのunitが生まれても追跡できるようにする。EPUBにPDFのページ番号・bbox・Docling refを必須化しない。

翻訳開始後、翻訳LLMにunitの分割・統合・ID変更をさせない。大きすぎるunitを分割する場合も、workflow側で元unitとの対応と範囲を保持する。

PDFのDocling refやbboxによる追跡は、そこから導いた原文の正確さの証明ではない。広いitemを分割した場合に、文字単位の位置精度があると偽らない。EPUBのnode位置も参照元snapshotに結び付け、異なる構造へ再解析した位置と混同しない。

## 9. 文書context・glossary・文体

### 自動準備と固定

文書context・文書glossaryは**自動準備を基本**とし、利用者の指定glossaryを優先する。異なる書籍の辞書をそのまま流用しない。

初期方式は、保存した翻訳前原文を章等のまとまりで読み、**用語候補・必要な用例・出典位置を収集し、文書全体で整理する方式**とする。著者の定義、略語、複合語、原表記を保つ語、文脈による使い分けを検討する。全文を単一のLLM呼び出しへ詰め込む必要はなく、一般的な分野知識だけで選んだ語を本文確認済みと扱わない。原本画像・コード本体・数式をこの準備のために渡す方式にはしない。

文書の主題・章節context、採用glossary、文体policyを**翻訳開始前に保存・固定**し、各翻訳chunkで同じversionを参照する。翻訳中にchunkごとに辞書を勝手に更新せず、全文や以前の会話履歴を毎回渡さない。

### 保存と方式の差替え

- **採用glossary**：346ページ検証の`glossary.v1.json`形式を基本とする。`version`・`book`・`style`と、原語をkey、訳語をvalueとする`terms`を持ち、原表記を保つ語は同じ文字列を指定する。翻訳はこの採用版を使用する。
- **準備の根拠**：候補・訳語案・用例・出典位置を、採用glossaryとは別ファイル・別形式で保存する。出典は原文snapshotに結び付け、準備結果と採用版の対応を追跡できるようにする。根拠全文を毎回の翻訳入力へ渡す必要はない。
- **実行記録**：準備方式・設定・model・時間・usage・参考コストをmanifest等へ保存し、補正・翻訳とは分けて集計する。

`terms`は単純な訳語指定であり、複合語の登録等で必要な区別を表す。本格的な文脈別ルールは先行して追加しない。正確なJSON schema・版の表し方・原文参照の具体形は、共通の原文block・翻訳unitのデータモデルと合わせて具体化する。候補のスコアや詳細な採否状態管理は現段階で必須にしない。

翻訳側は準備方式ではなく保存した採用版に依存させ、品質・時間・費用に問題があれば準備部分だけを差し替えられるようにする。初期実装は一方式に絞り、未使用の代替方式や汎用plugin基盤は作らない。黙って別方式へ切り替えない。方式だけが変わり採用glossary・context等の翻訳入力が同じなら、それを理由に再翻訳しない。

### 文体と変更

本文・説明文は**ですます調**で統一する。見出し・短いlabelは自然な体言止めを認める。chunkの境界で文体を変えない。図内文字・数式の英語label・program identifier等の保護対象には、訳語の置換を適用しない。採用glossaryの`style`と文体policyは、同じ文体指定から生成して独立した二重管理を避ける。

glossary・文体policyを変更した場合は旧版・旧訳を保持し、影響する訳を再検証・必要に応じて再翻訳する。原文取得・PDF補正はやり直さない。変更後も訳が適合していれば確認だけで再利用できるが、原語の出現検索だけで影響範囲を完全に特定できるとは限らない。影響を特定できなければ確認範囲を広げ、未確認の旧訳全体を新policyへ適合済みとしない。単純な訳語置換だけで完了と扱わず、関連QAと出力を更新する。

この自動準備方式の品質・時間・費用、および変更後の自動差分再翻訳は未検証。346ページ検証での辞書作成方法とは区別する（§18）。

## 10. 翻訳LLMの契約

入力は固定ID付きunit、明示したcontext、文書glossary、翻訳policy。原本画像・原本ファイル（PDF／EPUB）・未検査の抽出下書き・コード本体・数式は渡さない。EPUBのXHTML全文を自由に書き換えさせず、文単位のcontextと必要なinline構造の保護情報を渡す。

- 内容を完全に訳し、要約・説明の追加・勝手な省略をしない。
- 原文を再補正せず、不確かならその箇所を報告する。
- 数字、literal URL、identifier、inline code、章節・図表番号を保持する。
- 表cellはcontextを参照して訳すが、行列の追加・削除・並替えをしない。
- 全入力IDを順序も含め1回ずつ返す。訳文と不確実性をJSONで返し、Markdownの組立てはしない。

翻訳の分割は、確定したunitと章節を尊重する。PDF補正用のページ分割と、翻訳用の文章分割を同じものに固定しない。EPUBは章節・resource・論理blockを基準に扱い、固定ページへ変換して分割しない。

## 11. model・thinking設定

PDFの補正と、両入力形式で共通の翻訳について、provider・model・thinking levelを独立して指定できる。共通設定を適用することもできる。EPUBの通常経路では補正LLMの設定は使用しない。

優先順位は、段階個別指定 → 共通指定 → 起動環境の`PI_PROVIDER`・`PI_MODEL`・`PI_REASONING_LEVEL`。Pi CLIのmodel設定を自動読込み・コピーしない。値がない場合は送信前に不足を示し、未知のmodelへ暗黙に切り替えない。pi-aiの公開metadataで正確なprovider／model・対応thinkingを解決する。

PDFの補正modelには画像対応が必要であり、実行前に確認する。テキストEPUBの翻訳modelに画像対応は必須としない。要求したthinkingと、確認できた実効値を区別し、不明な実効値は推測で記録しない。

検証で使った`openai/gpt-6.1-sol`／`high`は実績であって、最適設定や固定の製品既定値ではない。「補正high・翻訳low」の品質・費用は未検証。

## 12. pi-ai直接推論・認証・安全境界

開始前に入力形式・hash・所有／参照範囲・形式ごとに必要な環境・helperとprovider認証の可用性を確認する。EPUBにDocling／OCR modelを要求しない。providerはOpenAI固定にせず、ChatGPTサブスクリプションOAuthを実動確認対象とする。catalog掲載だけでaccountの利用権を保証せず、認証失敗時に別mode／providerへfallbackしない。

補正・準備・翻訳・局所reviewごとに独立Node helper processからpi-aiの公開`builtinModels()`／`models.streamSimple()`を利用する。当該処理に必要なpolicy・JSON・画像だけでrequestを作り、tool宣言、探索、会話履歴、extensions／skills／project context注入、agent loop・自動回復を持ち込まない。Pythonが文書state・validation・有限retry・保存を所有する。

Python↔Nodeのschema version・request ID付きLF区切りJSONLと、modelの回答JSONを区別する。UTF-8とLF framingを連続消費し、U+2028／2029をrecord境界としない。process終了コードだけでは成功とせず、pi-aiのterminal done／error、最終AssistantMessageのstop reason・provider／model、helper正常終了とPythonの回答JSON／schema／ID・根拠validationを確認する。`stream.result()`はerror／abortedでもmessageへresolveするため、例外がないだけでは成功にしない。stop以外やtoolCallを採用しない。**Pi sessionの`agent_settled`は要求しない。**

画像はPythonがPNG・寸法・順序・hash・base64 byte量・座標系を検査する。modelのinput limitsに応じた縮小は最終画像・変換率・実寸を記録し、画像の黙った省略で成功させない。CLIと同じmodel名だけで実効入力・品質の同等性を主張しない。

認証はpi-aiの公開API／CredentialStore interfaceを使い、独自OAuth／refresh protocolを実装しない。アプリ用の最小credential JSON（0600）とOpenAI login用の安定したinstallation UUIDをworkspace外のユーザー専用directory（0700）へ置く。Pi CLIのauth.json等を自動読込み・コピーしない。proper-lockfileでファイル全体のread→modify→atomic保存／deleteを排他し、追加OAuth fieldを保持する。callbackのundefinedは変更なしとし、壊れたJSONを空credential扱いしない。OpenAI OAuthは公開login APIへ`getDeviceId`と`agentName: "doc-translate"`を渡す。秘密入力・取消は公開interaction契約に従う。

送信前に非秘密の認証source／typeと送信先を明示する。token・headers・秘密URL・raw環境変数・秘密をechoし得るstderrを無加工でlogに保存せず、credentialをworkspace／exportへコピーしない。OAuth時にAPI key overrideを勝手に注入しない。

SIGINT／SIGTERM／pipe切断はAbortSignalで停止しchildを回収する。ただしOAuth refresh中は即時exitせず、pi-aiの取消から切り離された15秒timeoutのrefreshとstore保存をdrainする。Pythonは原則30秒のgraceを確保する。SIGKILL／disk障害時に保存保証できなければ明示エラーと再login案内を出し、空credential化や旧tokenへの無条件rollbackをしない。

PDF・EPUB・抽出文・コード例は未信頼dataであり、命令として実行しない。shell／API例の実行やリンクの自動fetchをしない。翻訳・準備に原本file・画像・code本体・数式・XHTML全文を渡さない。Node helperに文書artifactの変更やsnapshot採用を任せない。

原文・画像をproviderへ送る範囲・hash・送信先を明確にする。PDFの`--pages`は所有範囲かつ既定送信範囲とし、外側は明示`--reference-pages`だけを参照専用として許可する。範囲外文書・別providerへの切替・新たな大型model取得を無断で追加しない。事前費用見積りを必須の停止点にはしない。

Node開発はpnpm、初回依存導入はinstalled npm／pnpmの固定lockで明示実行する。compile済みhelper・manifest・両lockをwheelに同梱し、通常起動はNodeだけを使用する。実行時compile・自動install／npx／manager取得をしない。導入directoryはworkspace・credentialと分離し、manager／lock／helper versionごとに別runtimeを用意する。保存結果だけのstatus／render／reviseにNode・認証を要求しない。これらの操作と認証・推論helperは後続実装であり、現時点のcodec実装と区別する。

## 13. QAと完了の意味

| 検査対象 | 確認すること | それだけでは確認できないこと |
| --- | --- | --- |
| PDF補正の構造 | 所有ページの被覆、型、根拠ref、継続情報、表・リスト等の整合性 | 原本に対する読取りの完全性 |
| EPUB抽出の構造 | 本文resource・対象nodeの被覆、読み順、脚注・リンク・表・リスト等の対応 | authored構造や抽出対象の分類自体の正しさ |
| 翻訳の対応 | 入出力IDの一致、未知ID・欠落・重複・空訳 | unit内部の意味の欠落や誤訳 |
| 保護対象 | 翻訳前後のコード・数式・図・表構造の不変性、literal等の保持 | 補正時点で原本を正しく復元できたか |
| 文書全体 | 用語・文体・読み順・章節・図表等の対応 | 任意PDF・EPUBについての無誤りの保証 |

構造・対応関係・保護対象・参照関係の機械検査は全範囲で行う。追加LLMによる内容レビューは必要な範囲とcontextに限定し、全文の追加レビューは標準では必須にしない。QAレポートには機械検査の結果と、誰がどの範囲を内容レビューしたかを分けて記録する。機械検査や追加LLMレビューのいずれも、見つからなかった誤訳がないことの保証にはしない。

電子版PDFの独立した文字抽出や単語計数は欠落候補を探す補助とする。図内文字や除外内容も区別して扱い、単語集合の一致を意味・順序の正しさの証明にしない。スキャンに同じ独立比較元があるとは想定しない。EPUBでは元のDOM・読み順・対象nodeとの対応を補助検査するが、翻訳unitのID一致だけで、本文抽出時の脱落まで検出できるとは扱わない。

処理状態と内容確認状態を分け、少なくとも次を区別して表示する。

- 出力まで到達した範囲／未処理／処理失敗。
- 構造・対応関係の検査結果。
- 原本自体の欠損、未読箇所、接続や翻訳の要確認事項。
- 誰が、何を、どの範囲まで内容確認したか。

未処理・処理失敗が残る出力は「部分出力」とし、指定範囲の受理済み結果が揃った場合にのみ「全文処理完了」とする。その場合も内容上の要確認事項は残り得る。

一部失敗や要確認箇所を隠して「完全翻訳」と表示しない。原本の欠損を忠実に保持したことと、処理が失敗したことは別である。modelの自己申告や空のuncertainty配列だけで、内容確認済みへ移行しない。

## 14. 保存・再開・局所修正

成果物は消失しにくいworkspaceへ保存し、入力・policy・glossary・model設定・schema version・attempt・状態・hashを記録する。中間結果と未完了の回答を区別し、検証済みartifactだけをatomicに確定する。

再利用判定で照合する主な入力：

| 段階 | 一致を確認する主な入力 |
| --- | --- |
| 原文取得 | 原本文書のhash・入力形式・対象範囲、形式別の抽出処理version・設定。PDFではDocling／OCR、EPUBではparser・構造抽出規則等 |
| PDF補正 | 渡した抽出下書き、所有／参照範囲、画像hashと描画条件、context、補正policy、schema、provider／model／thinking |
| 翻訳 | unitの原文・ID・context、言語、glossary・文体／翻訳policy、schema、provider／model／thinking |
| rendering | 採用する翻訳前原文・翻訳snapshot、構造情報、図assets、出力形式と生成処理のversion・設定 |

**再利用できる根拠があれば使い、対応が曖昧な旧訳は無理に引き継がない。** 原文blockのIDだけで古い訳を使わず、旧訳との対応と実際の翻訳入力を照合する。原文block・unitの分割や統合で対応が曖昧になった場合は、旧訳を機械的に分割・接合して流用せず、元の対応・変更経緯を残して必要な範囲を再翻訳する。

コードの内容だけが変わり、本文の翻訳unit・context等が変わらなければ、本文全体のLLM翻訳をやり直す必要はない。影響するrendering・QAは更新する。

未完了attempt、保存途中、確定済み結果を区別して再開する。同じworkspaceへ重複実行して状態を壊さない。変更後の再利用根拠が不明な結果は無条件で有効とせず、旧成果物を保持したまま再検証する。

346ページ検証では、進行中呼び出しを完了・検証させてから停止し、確定済み55ページをhash照合して再利用した（§18）。任意時点の強制終了からの復旧、設定・glossary変更後の自動差分処理、block再分割後の一連の局所修復は未検証。実験の限定的な再開実績を、汎用的な復旧機構の完成と扱わない。

## 15. retry・concurrency・入力上限

retryは有限とし、provider内部のretryとworkflowの再試行を別に記録する。失敗chunkのために完了結果を破棄して全文を再送しない。

入力・出力上限に達した場合は、要約させず、対象や分割を調整する。読めない内容を成功させるためにmodelが補完した結果を採用しない。

PDF検証時の初期値は、補正が所有5ページ＋前後各1ページ、画像長辺1800 px、翻訳source約12,000文字、各段階concurrency 2。EPUBに所有ページ・画像条件は適用しない。これらはPDFでの実績値であり、任意PDF・EPUBに対する最適値・固定制限ではない。346ページ検証ではPi内部のretryを無効にし、workflow側は初回を含め最大3試行で実行した。これは過去の検証条件である。新実装は呼出しtimeout 1200秒、workflowは初回を含め最大3試行・待機3／6秒を初期値とし、pi-ai provider optionsに`maxRetries:0`を明示する。adapterでの反映を確認し、未確認の内部retryを0と断定しない。認証・範囲・隔離違反は闇雲に再試行しない。

## 16. 最終出力と将来の出力形式

### 現在の対象：Markdown

主成果物は日本語Markdownと相対参照可能な図assets。併せて以下を残す。

- 翻訳前原文JSON（PDFの補正済み原文／EPUBの構造取得済み原文）、訳文対応表、構造情報、閲覧用の英語Markdown。
- 原文block・翻訳unit・訳文から、PDFの元ページ・ref、またはEPUBの元resource・要素／nodeへ戻れる対応表。
- 文書context、採用glossary、policyのsnapshotと、glossary準備の根拠ファイル。
- manifest、入出力、修正履歴、QA・要確認レポート。
- 各呼び出しと全体の利用量・時間の集計。

コード・数式はPythonが保存した翻訳前原文の内容を使い、翻訳LLMに再出力させない。コード注釈はcode fenceの外で対応を表示する。単純表はcellの訳文を元の行列へ戻す。

構造を取得できた複雑な表は、**Markdown内にHTMLの`table`として出力**し、行・列・セル結合を保持してcellの訳文を戻す。Pythonが保存した構造から生成し、EPUBのHTMLを無加工で貼り付けない。構造を確定できないPDFの表は原本画像を表示し、EPUBの表は元table構造・関連resourceを保持して、要確認事項を残す。HTML表示への対応はMarkdown viewerに依存する。

数式は取得した表現を保ち、無理に一形式へ変換しない。LaTeXで保存した式はMarkdownの数式記法、安全に埋め込めるMathMLはMathML、画像として存在する式は画像で出力する。MathMLからLaTeXへの自動変換は必須にしない。元の数式データを保持し、安全に表示用出力へ載せられない場合も黙って省略せず、原データへの参照と要確認の目印を残す。数式記法・MathMLの表示もviewerに依存し、表示品質は未検証。

**要確認箇所には`※`だけを付け、説明文は本文に挿入しない。** 目印は該当段落の末尾等へ出力時に付加し、コード・数式の中や保存する原文・訳文そのものには混ぜない。QAレポートに対応する確認理由・元ページまたはresource・原文位置・Markdownの該当箇所を記録する。処理結果のサマリーには要確認件数とQAレポートの場所を表示する。目印の具体的な配置と対応の見やすさは未検証。

EPUBから取得したinline構造・リンク・脚注の対応と画像altを保持する。Markdownへ変換するときは参照先を出力先のanchor・assets等へ対応付け、元のXHTML相対pathをそのまま残してリンク切れにしない。対応不能部分は元の参照情報を残して明示する。

### 将来のPDF・EPUB出力

保存した原文・訳文・構造情報・assetsから、形式ごとの生成処理を追加できる構成とする。形式追加だけを理由に補正・翻訳をやり直すことは原則不要とし、該当する出力生成・出力QAを実行する。追加の翻訳対象textやcontextが必要になる場合は、その範囲を別途扱う。

| 出力形式 | 位置付け | 追加時に扱う主な事項 |
| --- | --- | --- |
| Markdown | 現在の実装対象 | 見出し、コードフェンス、表、画像参照 |
| PDF | 将来の拡張候補。未実装・未検証 | 日本語fontと組版、改ページ、長いコード・表、数式の描画、文字や図表の切れ |
| EPUB | 将来の拡張候補。未実装・未検証 | XHTML等の文書生成、章・目次・読み順、リンク・脚注・画像、言語metadata、packageの検証 |

翻訳QAと出力形式ごとのQAを分ける。形式ごとに、内容の脱落、画像・参照先、コード・表・数式の保持等を検査し、必要な場合は描画結果も確認する。内部でHTML等を利用することは妨げないが、正本から必要な構造を失う変換を前提にしない。

将来のPDFは、まず翻訳結果を読みやすく組み直した文書を想定する。元PDFと同じ配置へ訳文を差し込むことは別の要件であり、保証しない。

現在はPDF・EPUBの出力生成用の実装・ライブラリ選定や、汎用plugin基盤を先行して作らない。形式追加の具体的なCLI・保存形式・生成手段は、その機能を対象にするときに確定する。

図内文字の翻訳、PDFリンク注釈の復元、元PDFと同じ組版、DOCX等への書出しは、現時点の範囲に含めない。本文中のliteral URL・図表番号は保持する。

## 17. 利用量とコスト記録

文書context・glossary準備、補正、翻訳、局所レビューを分け、成功・失敗・retryを含む呼び出しごとの実績を保存する。

- provider／model、要求thinking、確認できた実効設定。
- input／output／cache／reasoning等、取得できたusageの生値と意味。
- 時間、終了状態、retry、参考コストと使用した価格情報。
- Docling等のローカル処理時間と、補正／準備／翻訳／reviewの段階別時間・全体wall time。
- helper／pi-ai／依存version、使用manager・lock hash、policy・画像・model・thinking budgets／output budget・transport／timeout／retry等の実効入力と、要求値／確認できた値／不明の区別。

providerごとのusageの意味を尊重し、reasoning等を二重加算しない。失敗時にusage不明なら0とせず不明と記録する。

model価格表換算と実請求を区別する。OAuth／subscriptionの利用枠、請求明細、親セッションの作業分を確認していない場合、それらを含む実費だと表示しない。

## 18. 検証済みの根拠

### 29ページ：同じ書籍の電子版・画像のみPDF

対象は`sample/book.pdf`、29ページの同じ英語技術書。原PDF SHA-256は`159a7136ef8f369d6762c7ab8d51c07a83b84ad5323d5729be10c212d3a1f165`。

検証環境：Docling 2.142.1／docling-core 2.104.0、Pi 1.4.0、`openai/gpt-6.1-sol`へ両段階`high`を指定。thinkingの実効値をeventから独立に確認できたわけではない。

| 項目 | 電子版 | 画像のみPDF |
| --- | --- | --- |
| 全文処理 | 29ページ | 29ページ、text 0文字・約154 dpiの可逆画像 |
| 補正後 | 250block、235翻訳unit | 同じ件数 |
| ページ間接続 | 15か所、情報矛盾0 | 同じ |
| 保持した内容 | code 9、formula 2、table 1、picture 11 | 同じ |
| 入力条件の比較 | 基準 | Pi画像は全29ページ同一。補正本文の正規化単語列が全ページ一致、code 9件のtoken列も一致 |
| 局所レビュー | 注釈分離1か所、訳語1unitを修正 | 注釈分離1か所、訳語8unitを修正 |
| 残った問題 | 汎用的な自動注釈分離・list depth等 | 同じ。末尾chunkの文体揺れも残った |
| Pi本処理時間 | 636.84秒 | 614.60秒。別にDocling OCR 131.25秒 |
| レビュー込み参考USD換算 | 1.031770 | 1.050782 |

既知のcode 3例・分数・表は、原本画像から作成した参照データでも照合した。参照データをPiには渡していない。独立した元PDFテキストとの単語計数で8,743語の欠落候補0だったが、翻訳の意味全体の保証ではない。

原本9ページで右端が切れたコードは、見えない続きを補わず保持した。注釈・訳語の問題は、初回自動処理後の局所修正で対処したもので、最初から自動で解決したとは扱わない。list depthはDoclingのインデントで補助しただけで、明示的な親子関係の自動出力は未実証。

各条件1回、同じ書籍からの2種類のPDFである。訳文の確認者は検証を進めたassistantであり、独立した人間の翻訳者による全文査読ではない。別書籍・実スキャンへの一般化、model最適化、時間・費用の統計的benchmarkではない。

検証資料の保存先は`/Users/minoru/.agent/experiments/translate-pdf/`。29ページ検証は一時ディレクトリから以下へコピーし、保存対象ファイルのSHA-256一致を確認した。元の成果物は削除していない。`__pycache__`は保存対象外とし、認証ファイル・symlinkは含めていない。

| 保存ディレクトリ | 内容 | 主な参照先 |
| --- | --- | --- |
| `book-29-electronic/` | 電子版の全文処理・局所レビュー・成果物 | `report.md`、`summary.json`、`run.py`、`review.py`、`render.py`、`audit.py` |
| `book-29-image-only/` | 画像のみPDFの作成・抽出・全文処理・比較 | `report.md`、`summary.json`、`make_image_only.py`、`inspect_docling.py`、`run.py`、`review.py`、`render.py`、`audit.py`、`preflight.py`、`compare.py` |
| `book-29-input-comparison/` | 上記が参照した抽出データ・原本画像由来の照合データ・過去の抽出方式比較 | `inspect_docling.py`、`docling/`、`pymupdf4llm/`、`pi-comparison/reference.json`、`pi-comparison/run.py`・`prepare.py`・`evaluate.py` |
| `book-29-two-stage-sample/` | 全文処理が共通helperとして参照した、指定4ページでの2段階検証 | `run.py`、`evaluate.py`、`report.md`、`reference.json` |

電子版・画像のみPDFの`run.py`は補正・接続・翻訳・初期出力、`review.py`は局所レビュー、`render.py`は最終版を使ったMarkdown生成、`audit.py`は照合を担う実験用コード。使用policy、glossary、元回答、修正履歴、図assetsも各ディレクトリに保存している。抽出方式比較は履歴資料であり、現在のDocling固定方針を変更する根拠にはしない。

**スクリプト・manifest・レポート内の旧絶対パスは、当時の記録として書き換えていない。** 旧`/tmp/translate-pdf-full.srJ6o4/`は`book-29-electronic/`、旧`/tmp/translate-pdf-image-only.m5hQKr/`は`book-29-image-only/`、旧`/tmp/translate-pdf-inspect.Zjbv9B/`の必要資料は`book-29-input-comparison/`、旧`/tmp/translate-pdf-two-stage.XGyGZT/`は`book-29-two-stage-sample/`に対応する。保存先へ置いただけで直接再実行可能になったわけではない。共通helper・照合データも保存先を参照して読むものとし、一時ファイルの存続を前提にしない。

model比較用kickoffは`book-29-input-comparison/pi-comparison/kickoff.snapshot.md`に保存した、指定4ページでの過去の比較指示である。29ページ全文処理の指示書とは混同せず、今回の整理を再実行の許可と扱わない。全文処理用の新しいkickoffは作成していない。

主要な実績と限界は本節だけでも分かるように残し、スクリプトは検証の根拠・参考コードとして参照する。保存先はローカルの資料であり、仕様の理解にコードの読解を必須とせず、実験用コードをそのままproduction実装や汎用テストとして使わない。

### 346ページ：別書籍の全文初版生成・構造検証・標本レビュー

対象は`sample/design-and-build-great-web-apis_P1.0.pdf`（*Design and Build Great Web APIs*）、SHA-256は`b87e890d6473a2a4ce1ed0c77003193f101555fb6f46f2a47baa2a904101e858`。別セッションの保存済み記録を確認して取り込んだもので、本設計議論では重複実行していない。

検証条件はPython 3.12.12、Docling 2.142.1／docling-core 2.104.0、Pi 1.4.0、`openai/gpt-6.1-sol`へ両段階`high`を指定。実効thinkingは未確認。画像長辺1800 px、所有5ページ＋前後各1ページ、concurrency 2、Pi内部retry無効、workflowは初回を含め最大3試行。

| 項目 | 確認済み結果 |
| --- | --- |
| 原文補正 | 346/346ページ・70chunk。所有ページの被覆と構造を検証 |
| 翻訳 | 全文188chunk・3,279unit。全翻訳ID対応が一致し、日本語Markdown初版を生成 |
| 試行・終了 | 補正74試行中4失敗はretryで解消。翻訳191呼び出しはpreview 3＋全文188。最終失敗chunk・実行中chunkは0 |
| 保持構造の出力件数 | code 255、picture 129、table 7、footnote 98。件数は分類の正しさや原本の完全保持の証明ではない |
| 内容レビュー | 独立Piで18区間・45ページを標本照合。指摘3件。人間の全文査読ではなく、標本外の意味品質は未確認 |
| 最終状態 | `processed_pending_quality_review`。**全文初版生成済み・構造検証済み・標本レビュー済み、内容品質は未承認** |

未解決の記録は境界28件、原文不確実性39件、literal差分候補555件、翻訳不確実性10件。原文不確実性には白紙や著者のコード欠陥も含まれ、差分候補には偽陽性もある。件数をそのまま転記ミス・誤訳の件数とは扱わない。

標本レビューの未修正指摘：

- **PDF内70ページ**：フォーム全体がロゴ画像と本文・表に分解され、図内文字まで翻訳された。翻訳Piへ画像やコードを渡さないことだけでは図の保持を保証できない。§6の図範囲と内部抽出内容の対応付けが必要。
- **334ページ**：前ページの親項目`npm`に属する下位項目の階層が失われた。§7の文章接続と親子関係の継続の分離が必要。
- **172ページ**：略称DARRTの展開語について、英語併記を推奨する軽微な指摘。訳文の局所修正は未実施。

全文翻訳前に、27–28ページの脚注を挟む本文継続を修正し、脚注を保持した。29–30ページのコード接続も確認した。進行中呼び出しを完了・検証させてから停止し、確定55ページをhash照合して、回答損失・再送なしで再利用した。任意時点の強制終了からの復旧や、policy・glossary変更後の自動差分再翻訳、block再分割後の一連の局所修復は未検証。

**検証時のglossaryは、Root Agentが書名・kickoffの分野情報とモデルの一般知識から46語を一括作成したもの。** Docling抽出中に`glossary.v1.json`を書き、固定した同じJSONを全翻訳chunkへ渡した。作成時点で目次・抽出全文・代表原文・ページ画像に基づく候補選定は行っていない。専用の用語抽出・別Piによる辞書生成・候補の人間承認はなく、実行中の改版もない。全46語の妥当性・書籍固有語の網羅性・全出現箇所の適用率は未確認であり、§9の本文全体を踏まえた自動準備の実証とは区別する。作成時のwrite記録と保存ファイルの一致は確認済み。

全体wall timeは約9,690秒（約2時間41分）、Doclingは303.7秒。補正・翻訳・標本レビューのprocess時間和はそれぞれ約10,940秒・5,468秒・1,191秒で、並列実行のため全体wall timeとは異なる。記録された翻訳段階wall timeはpreview・中断・待ちを含み、純粋な全文翻訳時間として比較しない。

| 段階 | input token | output token | reasoning token（output内数） | 参考USD |
| --- | ---: | ---: | ---: | ---: |
| 補正 | 4,009,318 | 501,530 | 23,028 | 13.837483 |
| 翻訳（preview含む） | 769,158 | 240,027 | 13,664 | 3.938586 |
| 標本レビュー | 570,260 | 49,287 | 5,955 | 1.633390 |

cacheRead／cacheWriteは0。合計参考USDは19.409459で、Piのcatalog換算であり実請求額ではない。親セッションのusageは含まず、reasoningはoutput／totalへ二重加算しない。

確認した保存先：`/Users/minoru/.agent/experiments/translate-pdf/design-and-build-great-web-apis-20261024T0213/`。

- 結果・確認範囲：`report.md`、`summary.json`、`status.json`、`qa.json`、`chapter-review.json`。
- 再開・修正の根拠：`recovery-checkpoint.json`、`review-changes.v1.json`、`root-agent-review.v3.json`。
- 原文・対応・出力：`corrected-document.v1.json`、`translation-units.v1.json`、`translation-map.json`、`source-map.json`、`book.ja.md`、`book.source.md`、`assets/`。
- 設定・利用量：`manifest.json`、`artifact-manifest.json`、`glossary.v1.json`、`usage.json`。
- 実行コード：`extract.py`（Docling抽出・ページ画像生成）、`run.executed.v1.py`（初回実行時snapshot）、`run.executed.v2.py`（修正後の再開時snapshot）、`run.py`（保存時点のrunner）、`test_run.py`（検証中に追加した回帰テスト）。実際の実行版はsnapshotと`review-changes.v1.json`を併せて参照する。
- 検証指示：`kickoff.snapshot.md`。repositoryにあった346ページ用kickoffとの内容一致を確認済みで、実験側へ集約した。保存された指示を、新しいセッションでの再実行許可とは扱わない。

この実験の成果物は`/tmp`には依存しないが、上記ローカル保存先の恒久的な可用性を保証しない。主要な結果・限界は本節だけでも分かるように残す。実験用runnerをproduction CLIや汎用テストとしてそのまま流用しない。

## 19. 残る実装詳細・未検証事項

基本方針への合意と、実装詳細の具体化・実証の完了を区別する。以下が残ることを理由に、合意済みの基本方針を未合意の提案へ戻さない。

| 項目 | 現状 | 今後の確認・具体化 |
| --- | --- | --- |
| 346ページの内容品質 | 全文初版・構造検証・標本レビューまで到達。指摘と候補の確認・修正は未完了 | §18の記録を根拠に扱う。追加の照合・局所修正が必要なら別途対象化し、全文処理を重複実行しない |
| 図の分類・ページ間の親子関係 | §6–7の基本方針は確定。汎用schemaと自動処理は未実証 | 図範囲と内部抽出内容の対応、文章接続と親子関係の分離を具体化し、既知の誤分類・階層欠落を確認対象にする |
| 原文変更・glossary変更後の局所処理 | §9・14の再利用方針は確定。変更後の自動差分処理は未検証 | 原文・context・版の対応を使う影響判定を具体化し、曖昧な旧訳を流用しないことを確認する |
| 障害復旧・resume | 安全に処理を止めた後の55ページ再利用は確認済み | 任意時点の強制終了、保存途中、API障害からの復旧は別途検証が必要 |
| 文書context・glossaryの自動準備 | 候補・用例・出典位置を集める方式と差替え境界は確定。品質・時間・費用は未検証 | 本文の用例・定義との整合、準備の利用量を確認する。分野知識から46語を作った実績と混同しない |
| 内容確認の方法・範囲 | 全範囲の機械検査＋必要範囲の追加LLMレビューを採用 | 内容レビュー範囲と未確認範囲を明示する。全文の追加LLMレビューは標準では必須にせず、検査結果を無誤りの保証と扱わない |
| 複雑な表・数式・要確認の目印 | §16の表示方針は確定。実表示・安全な生成は未検証 | HTML表、数式記法・MathML・画像、`※`とQAレポートの対応を、対応viewerとともに確認する |
| テキストEPUB入力 | EPUB 2／3のXHTMLリフロー型という受理範囲は確定。未実装・実データ未検証 | parser・対象判定・inline構造・脚注・リンク等を具体化し、構造保持と安全境界を確認する。PDF用OCR・補正LLMは必須にしない |
| ノイズを含む実スキャン、多段組み・複数ページ表・長いunit | 一般的な成立性は未検証 | きれいな画像のみPDFの実績と区別し、対応不能部分を省略・推測補完せず保持する |
| CLI・共通schema・保存・依存 | 初期package／help、PDF・EPUB位置union付き原文snapshot／OS lock、公開JSONL codecを実装。具体形はartifact契約と正式Planによる | kind別構造検証・入力adapter・unit／訳文・修正CLI等は後続。保持できることを意味検証済みとしない |
| pi-ai直接接続・認証 | 接続責務・credential・terminal eventの契約は合意済み。公開codecのみ実装 | metadata／setup-ai／認証store／generationを実装し、実認証と小範囲送信は個別承認で検証。§18は直接経路の再実証ではない |
| 初期CI・配布 | macOS ARM64の品質checks・offline codec／wheel導入検査を設定 | 今回および後続Work Itemではremote CIの実行・成功確認・実行ref／run URLの取得・記録を受け入れ条件にせず、そのためのcommit／pushも必須にしない。既存CI設定・smoke検査と、各Work Itemの責務に対応するローカル正式チェック・build／別環境導入検証は維持する。remote CIの未実行だけでは未完了・blockedにせず、未公開／未実行をremote成功としない。PDF importをOCR変換成功としない |
| 段階別の別model／低thinking最適化 | 未検証 | 中核方式と切り離して、必要になった時点で比較する |
| PDF・EPUB出力 | 将来の拡張候補 | 今回は責務分離のみ。生成処理・依存・形式別QAを先行して作らない |

設計上の決定と未検証事項を後続へ引き継ぎ、実装計画・production実装・追加の有料検証は本書の整理だけを根拠に自動開始しない。
