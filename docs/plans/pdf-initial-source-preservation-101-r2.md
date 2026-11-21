# #101 第2版：PDFの題名とコードを元の位置に残し、同じ版で原文保持を検証する

## 目的
既存の [#101](https://github.com/minorunakamura/doc-translate/issues/101) **1件**で、①所有81–85枚目を保存できない原因を直し、②その修正を含む最終版で従来の統合検証を完了する。①の合格を②の合格にはしない。枚数はPDF先頭を1とする。

**変更する合意は2点。** [直前の正式Plan](pdf-initial-source-preservation-101.md)（[正式コメント](https://github.com/minorunakamura/doc-translate/issues/101#issuecomment-7031165862)、SHA-256 `4912ff065835b831496ecd28ad3d1aa46990db204ebb184ee0123a03c94f5146`）の「新規採用規則は対象外／統合は非TDD」に対し、今回の保存前の振り分けだけを製品修正の対象とし、その規則だけをtest-firstで検証する。別の先行Issueは作らず、#98・#108・#99・#100は開き直さない。直前Planの115–116枚目の判定、他の全完了条件・保全条件は変更しない。旧Plan・コメント・固定見本は書き換えない。

## 停止理由と確認した事実
- HEAD `3439a5c` のRoot独立再現は `work/issue101-root-probe-kpSZIzZQ/{readout.txt,once.py,overlap.json,exit}`。空の隔離先で未編集の回答を閉じたpipeから1回だけ渡した。回答は検証済みだが、新規chunkは0。文字数42の同じ抽出項目に対し、題名（caption）が先頭21文字、コードが項目全体を所有しようとして保存前に拒否された。人工CLI成功・旧chunkの再読はこの失敗を覆さない。
- 原本PDFの描画文字を該当位置で照合すると、上から **題名／コード前半の行／コード後半の行**。共有項目には題名と後半が、別項目には前半があり、3行ともそれぞれの文字に一致する。回答の題名はそのコードを指す。これは局所の入力確認であり、修正後の出力や挿入空行数の証明ではない。
- `src/doc_translate/pdf_adoption.py` の `adopt_owned_ranges` と `_printed_role_split` は、[完了済み#108](https://github.com/minorunakamura/doc-translate/issues/108) の単一項目内の分割を扱うが、後者は別項目まで所有する根拠には使えない。[#99 第3版](pdf-initial-source-preservation-99-r3.md)の複数項目のコード復元も#108の分割条件を広げていない。この組合せに対する証明を**二重所有検査の前**に足すのであって、重複を許すのではない。

## 要件・決定事項
1. 新規採用では、同じ所有ページの、同じコードを明示的に指す題名とコードについて、共有項目とコードが指す他の項目を**一緒に**照合する。各項目の有効な出典範囲、原本の可視行、提案中の対応行を突き合わせ、間にある行も含めて、読める文字・記号・行内空白、役割・相手・表示順・回数を一通りに説明できる場合だけ、各文字範囲を一方に割り当てる。図表・周囲の本文・別のコード例・未所有範囲を取り込まない。単にbboxが重なる、近い、先頭が一致する、同じ文字列がどこかにある、という理由では分けない。
2. 題名は元位置で独立したcaptionとしてコードを指し、実際の翻訳unitに1回入る。コードの可読行は元順で各1回、英語出力の当該コード欄に残り、翻訳unitにしない。題名をコードに混ぜず、コードを題名に含めない。ページ・各出典範囲・対象関係・前後の本文順が保存後にも辿れること。コードの途中に別項目がある場合、共有項目のbbox内だけを拾った不完全な列を「全行」と数えない。
3. **所属と整形は別判定。** 所属が一意でも、提案の空行・字下げ等を証明できなければ、読める抽出文字・根拠のある改行を残し、該当行や境界・提案との差・理由を既存の位置付きQAで辿れるようにする。81/83枚目の既存の別箇所の不読記号を補わない。所属が一意でない、指定spanに反する、別候補に同じ可読範囲が必要な場合は従来どおり拒否する。QAだけへの退避・先勝ち・候補削除で保存しない。

## 制約・対象外
- #101の未コミットの `.pi/skills/verify-project/scripts/pdf_adoption_replay.py` と `pdf_connection_comparison.py`（同じディレクトリ）、未追跡の直前Plan、既存の部分実装・private証拠をそのまま引き継ぐ。検証helperは現在の差分を読んでから必要箇所だけ補完する。独断でcommit・stash・破棄しない。
- 書籍・原画像・生回答・詳細エラーはprivateのみ（通常stdout/Git/Issueに出さない）。旧Workspace・訳文・attempt・chunk・固定見本の修復／移行なし。trial 15の安全停止（累計15 trial／140予約）を維持し、外部trial markerには読取りを含めアクセスしない。実AI送信・追加retry・実認証・OCR・モデル取得・無断install・共有環境変更なし。ページ番号・ref・書籍固有文字列・固定座標・固定期待を製品判定やモデルに渡さない。
- 新しいページ間接続規則、一般的なPDFレイアウト解析、後段での補修、新CLI・永続状態・常設全冊E2E・新CIは追加しない。全書籍での接続差分は対象だが全冊翻訳・翻訳品質保証、EPUB、#65のrender誤警報は対象外。

## 実装方針
- `pdf_correction._make_chunk → pdf_adoption.adopt_owned_ranges` の共通採用経路と既存の描画・出典照合を使う。題名と全コード行の所有が一意な場合だけ、重ならないcharspanと正しい読順を組み立て、最後の重複拒否はそのまま通す。共有項目内の行順と複数項目を通した紙面順を区別する。正当な単一項目の分割、別項目が根拠なく混ざる既存の拒否対照、対象ID・fragments・locations・位置付きQAを維持する。
- 採否の意味が変わるので、既存の方式識別／schemaの仕組みで新規採用と再読を分ける。調査時の現行は `drawn-punctuation-v13`／schema 14。無指定を含む旧方式は当時の規則で回答から厳密に再構成し、byte一致を確認。旧方式の出力を新方式へ昇格・書換えせず、方式未知・改変・取り違えや不正なorphanを救済しない。新規保存・fresh load・成功再利用・resume／候補採用を同じ公開契約で確かめる。
- 文書は `docs/reference/artifacts.md` に新規採否と版別再読、`docs/reference/usage.md` に英語コード・題名と要確認の読み方、`.pi/skills/verify-project/` の関係手順・Feature Mapに今回の再実行方法を不足分だけ記す。正式チェックの入口変更時だけ `CONTRIBUTING.md` を更新する。

## 完了条件
**① 81–85枚目の局所修正。** 所有5枚すべてを含む元の保存回答を未編集で再生し、新方式でchunkを新規保存、別processで厳密に再読する。原画像と、採用chunk → sourceの出典・対象関係 → 実units・英語・位置付きQAを突き合わせる。81枚目は題名1回・その下にコード2非空行が原文どおり各1回で、前後の本文順も正しい。空行数は提案を根拠に断定しない。81/83の局所不読は該当位置と前後の可読内容の両方を残す。成功chunkのコピー、例外を握り潰すだけ、所有を一律拒否するだけでは不可。

**② 同じ最終版での統合。** [直前Planの「要件・決定事項」「完了条件」「動作確認方法」](pdf-initial-source-preservation-101.md)は上記の明示変更を除き必須。①のchunkを母集団から除かず、成功分だけの比較にしない。

| 対象 | 統合で直接照合する結果 |
|---|---|
| 所有本文・正常対照 | 境界75と76–80の欠落50項目を隣接chunkまで照合。出典・全文・所属・順序・回数・連続性を実unit（保護token展開）または英語出力で確認。参照ページの二重採用なし。[#97 第5版](pdf-initial-source-preservation-97-r5.md)の固定9対照を含む。 |
| コード・本文接続 | コード115–116、117–118、119–120–121、185–186、187–188の5系列と本文64→65、123→124、213→214の3組。118可読行、185脚注、187caption、周囲・callout・独立図の全領域・位置・順を保持。本文記号の置換3・挿入1を接続前chunk・実unit・英語で確認。[#99 第3版](pdf-initial-source-preservation-99-r3.md)・[#100 改訂](pdf-initial-source-preservation-100.md)参照。 |
| 115–116の表示 | [#98 第3版](pdf-initial-source-preservation-98-r3.md)どおり115のcaptionは独立して1回、コード4非空行、116は29非空行。各行全文・内部空白・順・相対字下げ、116の第3非空行は本文と揃う表示0、第1は2。**出力後**に固定見本と全29行を比べて各行−2桁を別記。共通余白の理由・提案との差は各行を追える位置QA、不明な追加空行は各境界の前後両行の位置QA。抽出済み区切りを失わず、8空行復元・41行完全一致・元コードの絶対字下げとはしない。 |
| 未解決・保護対象 | 73→74と61→62は分離し、両端・位置付きQA・失敗checkを残す。119–121の既知21行の字下げ残差と追加残差は接続成功と別記。81/83・190の不読、211の正常な履歴、321のchecklist親子・索引、346の6×2全12セルの内容・URL・数値と位置を実出力まで照合する。 |
| 全書籍の接続差分 | `work/issue83-survey-xcP1KLJ7/root-survey/` のinventory・populationとseed対応表で対象を固定。旧／新の全接続境界から追加・消失・相手変更を列挙し、変わった全組を原画像・前後の所属・位置・順・出力で判定。固定8組や過去の11組に限定しない。失敗・未照合があれば統合未達。 |

## 動作確認方法
- **TDDは新規の振り分け規則のみ。** `tdd` Skillと `tests/test_pdf_correction.py` の小さい自作PDFを使用。紙面に「題名→コード前半→後半」、抽出には「前半」と「題名＋後半」がある例を公開 `correct_pdf → load_pdf_correction → assemble_pdf_source` に通す。修正前の保存拒否をRedとし、修正後は役割別の全文・順序・target・各出典範囲が残ることを確かめる。既存の `test_shared_caption_and_listing_cannot_absorb_another_ref`（無関係な別項目を含む両側）、反復文字・targetなし・通常コード行の注記化の拒否、および従来通る共有題名・脚注、改行・字下げを維持する。既存拒否を通すための期待値変更は不可。
- **実書籍・互換性の直接再生、CLI、統合、文書は非TDD。** [verify-project](../../.pi/skills/verify-project/SKILL.md) と [Feature Map](../../.pi/skills/verify-project/references/feature-map.md) のPDF原文標本、保存記録、#97/#98/#108/#99/#100を使う。原本・raw・元request/回答は `work/design-and-build-great-web-apis/` を読取専用、①の紙面参照は上記surveyの `seed-81/`。新規0700親の空workspaceで、所有・参照・projection・画像hashを確認し閉じたpipeで各未編集回答を1回だけ渡す。旧成功をコピーせず、新規保存→別process load→assemble→実units・英語render・QA。比較資料は出力後だけ、surveyと同親の `root-final/{fixed-expectations,missing-content-gold,code-gold,hashes}.json` と原画像を使用。実8件・23期待／対象15地域、116の行特定用資料等は直前Planどおり。実providerの再現性は主張しない。
- 別の新規0700親で #97/#100 の人工PDF・実Node/faux/no-networkによる通常run→別processのresume。省略された所有本文が翻訳requestにあり、未所有部分は入らず、根拠のない記号変更は採用せず位置QAが残ること、成功済み再送0・immutable byte/mtime不変を確認。旧方式の再読／再利用と改変・未知方式の拒否も確認する。
- 既存の `pdf_adoption_replay.py`、`pdf_{listing,role,connection,body}_comparison.py` と現在の部分実装を再確認。旧#98/#108はschema 11、#99は13、#100は14の限定標本で、①・②の成功証拠ではない。共通 `create-verification-skill` による最小補完の初期対象は①の多出典の行→役割→実出力と②の現行統合・全接続差分。既存手順で足りる部分は再利用し、旧assertを緩めず、同じ保証内で必要な手順・小さなhelperだけ更新保存し再実行する。未予定の補完だけで再計画不要。独自runnerや安全条件の迂回は不可。
- Workerはfocused回帰・実操作・手順再実行。Rootは別の新規親で①と②の原画像・実出力・位置QA・全変更組を直接照合する。revision/dirty・入力指紋・command/exit・観測／未達をprivateに残し、所有する一時物・childのみ回収、証拠再読と元資料hash/mtime不変を確認。`CONTRIBUTING.md` の影響選定・順序で補正、source、units、render、run/resume・候補採用の関係回帰、helper、Ruff check/format・PDF環境ty。共通schema／保存に及ぶためcore全pytestと正式PDF phaseも実行し、最終差分で独立Review・人間のCode Reviewを受け直す。
- これまでの再現・検査・承認は修正後の検証に流用しない。別の失敗を見つけても除外せず記録する。許可範囲で安全に操作できなければ不足・試した方法・再開条件を示してBLOCKED。別の製品変更や保証・期待の変更が必要ならREPLAN_REQUIRED。未確認を成功に数えない。
