# #101 改訂：通常PDFの翻訳入力・英語出力・QAで原文保持を統合確認する

## 目的
[#101](https://github.com/minorunakamura/doc-translate/issues/101) は、完了済みの [#98](https://github.com/minorunakamura/doc-translate/issues/98) と [#100](https://github.com/minorunakamura/doc-translate/issues/100) を含む成果が、**同じ最終版の製品経路で同時に成立するか**を検証する。先行Issueの合格を今回の結果として転記しない。

**今回変えるのは #101 の115–116枚目の採点だけ。** [元の全体Plan](pdf-initial-source-preservation.md)「確認した原因・照合資料」「完了条件」の41行・字下げ完全一致に代えて、下記の [#98 第3版](pdf-initial-source-preservation-98-r3.md)「要件・決定事項」を使う。[正式な第3版コメント](https://github.com/minorunakamura/doc-translate/issues/98#issuecomment-7007856698)とも一致する。固定見本は以前の変換結果で、著者の元ファイルではないためである。元のPlan、先行Issue、固定資料は編集しない。その他の統合条件は元のPlan全文を維持する。

## 要件・決定事項
枚数はPDF先頭を1とする。次の判定は相殺しない。

| 判定 | 必須の観測と報告 |
|---|---|
| 紙面と英語出力 | 115枚目（紙面101ページ）はファイル名が元位置にcaptionとして1回だけあり、コード4非空行と混ざらない。116枚目は29非空行。fresh load後の各断片を結合後の英語コード欄に対応させ、各行の全文（内部空白・記号を含む）・順序・回数・相対字下げを原画像と照合する。コード自体は翻訳unitにしない。1行への退避、欠落、入替え、QAだけにある行は不合格。 |
| 116枚目の共通余白と固定見本 | 紙面の最左行を表示0桁とする。第3非空行は直後の本文と同じ x=90 pt で表示0桁（見本2桁）。第1非空行は x=99.344 pt で表示2桁（見本4桁）。出力後、全29行を未変更の見本と個別比較し、**出力−見本が各行とも−2桁**であることを別記する。対象行・出典・確認できた行頭差・提案と採用表示の差・元コードの絶対字下げを断定できない理由が位置付きQAから辿れること。全行を特定できれば同じ警告を29件作る必要はない。紙面に一致し、この共通差だけなら余白項目は合格。個別のずれや任意の平行移動は不可。「見本と完全一致」「絶対字下げを復元」とは数えない。 |
| 追加空行 | 抽出済みの改行・空行は残す。提案にしかなく数を裏付けられない空行は足さない。各不明境界で**前後両行**の位置・出典、提案と採用の差、不明理由を出力QAから特定できること。片側のみ、共通余白のQAでの代用、抽出済み区切りの消失は不合格。これを満たせば追加数不明は許容するが、見本の8空行の復元・41行完全一致は報告しない。 |

行の取り違えを防ぐ参照は `work/issue98-r2-root-4XdxFccP/paper-anchor.json` の `body` と `rows`。これは原本の測定で、#101の出力証拠ではない。紙面合否・固定との差・各QAの有無を実測で残す。この例外を他ページに転用しない。

## 制約・対象外
既存の未コミット変更・部分実装・追跡外の証拠を保持する。独断のcommit・stash・破棄、旧Workspace・訳文・attempt・chunk・固定見本の修復／移行はしない。trial 15安全停止（累計15 trial／140予約）は維持し、外部trial markerには読取りを含めアクセスしない。実AI送信・追加retry・実認証・新OCR・モデル取得・無断install・共有環境変更なし。書籍本文・画像・生回答・詳細エラーはprivate限定でstdout・Git・Issueへ出さない。ページ・特定ref・文字列・座標・既知の2字差を製品の採否やモデルへ渡さない。全書籍の**接続差分**は対象だが、全冊の翻訳・品質保証、EPUB、#65のrender誤警報、製品の新規採用規則や後段修復は対象外。

## 実装方針
1. 再開時のHEAD・差分・保存方式を確認し、原資料から期待を使わず新規採用したchunkを別processで再読する。調査時は `3439a5c`、`src/doc_translate/pdf_correction.py` の `drawn-punctuation-v13`／schema 14。公開経路は `correct_pdf → load_pdf_correction → assemble_pdf_source → generate_translation_units / render_document`。旧記録の再読と新規の採用を混ぜない。
2. 原本・raw・保存request／未編集の回答は `work/design-and-build-great-web-apis/`。母集団は `work/issue83-survey-xcP1KLJ7/root-survey/` のinventory・population・各seedの対応表。比較は同親の兄弟 `root-final/{fixed-expectations,missing-content-gold,code-gold,hashes}.json` および各seedの原画像を使う。正解は製品出力後の比較専用。元Planの実8件・23期待と対象15地域を基準に、対象ごとに出典→断片→組立→実unitまたは英語anchor→QAを対応させる。
3. 文書は `docs/reference/usage.md`（利用者が英語コードとQAをどう読むか）、`docs/reference/artifacts.md`（採用・出典・再読）、`.pi/skills/verify-project/`（現行統合の再実行手順・Feature Map）を確認し、不足箇所だけ更新する。正式チェックの入口が変わる場合だけ `CONTRIBUTING.md` を更新。過去の手順の適用版を書き換えない。

## 完了条件
上記3判定に加え、以下を**同じ最終版**で満たす。数だけ、部分出力、過去版の成功は代用にならない。

- **所有本文：** 境界75と76–80の欠落50項目を隣接所有chunkまで通して、全文・所属・位置・順序・必要回数・連続性を照合。翻訳対象は保護tokenを展開した実unit、非翻訳部分は英語出力に届く。参照のみの二重採用なし。固定9対照を含め [#97 第5改訂](pdf-initial-source-preservation-97-r5.md) の局所結果と残差を区別する。
- **コード5系列・本文3組の接続：** 115–116、117–118、119–120–121、185–186、187–188 と、64→65、123→124、213→214。各断片の全文が正しい位置で1回、両端の出典から実出力まで追えること。118の可読行、185脚注、187caption、周辺段落・calloutを失わない。本文の記号4か所（置換3・挿入1）は接続前chunk・実unit・英語にあり、各独立図の順序・所属・画像全体を別に検査する。局所の対応・対照は [#99 第3版](pdf-initial-source-preservation-99-r3.md) と [#100 改訂](pdf-initial-source-preservation-100.md) の「完了条件」。
- **全書籍の接続差分：** 固定8組に限らず、保存された当該書籍の全接続境界を母集団に旧／新の追加・消失・相手変更を列挙し、変化した組を一つずつ原画像・前後の所属・順・出力で判定する。旧Probeの「11組」は今回の期待件数ではない。未照合・意図しない結合は受け入れない。
- **未解決と保護対象：** 73→74（誤target）、61→62（脚注・見出し・独立本文3件が介在）は結合せず、両断片・位置付き接続QA・失敗checkを残す。119–121の既知21行の字下げは行別の残差・位置QAを示し、接続成立とは別判定。追加の残差も隠さない。この2境界と21行を回復成功に数えない。81/83の局所不読と190のfilenameは値を補わず、位置・隣接文・QAを照合。211の正常な履歴、321のchecklistの親子・索引の順と回数、346の6×2表の全12セルは個々の位置・内容・URL・数値・出典を原画像・実unit・英語表まで確認する。
- **通常経路・保全：** 下記の人工run／fresh resume、新旧の方式別再読／再利用と改変・未知方式の拒否、元資料の不変、正式チェック・独立Review・人間Code Reviewを満たす。欠落をQAへ逃がす、失敗したchunkを集計から除く、不明を成功数に入れる方法は不可。

## 動作確認方法
- **非TDD。** #101の仕事は統合出力の検証であり、先行Issueの採用規則のTDDを繰り返さない。自動回帰は既存の補正／source／units／render／run／resume／候補採用の関係ケースを使う。人工の文字差（Docling値を保ち位置QA）、一意でない接続や途中本文の拒否も維持する。原画像と利用者出力の対応はRootが直接確認する。常設全冊E2E・新CI・汎用runnerは追加しない。
- **書籍再生：** [verify-project](../../.pi/skills/verify-project/SKILL.md) と [Feature Map](../../.pi/skills/verify-project/references/feature-map.md) のPDF原文標本準備・保存記録・#97/#98/#108/#99/#100各節を使う。新規0700親の閉じたpipeで所有／参照・projection・画像hash・回答の対応を確認して無送信で再生し、新規保存→fresh load→実units・英語・QAを取得。旧attemptや成功chunkを新規成功としてコピーしない。実providerの再現性を証明する試験ではない。
- **手段の不足：** `pdf_adoption_replay.py` と `pdf_{listing,role,connection,body}_comparison.py`（Skillの `scripts/`）は局所の観測・照合に使えるが、旧#98/#108はschema 11、#99は13、#100は14の3 chunk限定。旧assertを緩めたり過去のscoreを足したりしない。共通 `create-verification-skill` の初期対象は、現行版での標本の統合照合と全書籍の旧／新接続差分の列挙・原画像への対応。Implementation Rootは手元の手段を再確認し、既存手順の組合せで足りれば重複作成せず、不足分だけ関連手順・小さいhelperを補完・保存し、更新後に再実行する。
- **人工CLIは別検査：** 別の新規0700親でMapのPDF run／resumeと#97/#100の人工PDF、実Node／faux／no-networkを使用。省略された所有本文が実翻訳requestにあり、未所有本文を入れず、不採用差に該当位置のQAがあること、別processのresumeで成功済み再送0・immutable byte/mtime不変を確認。旧成功は元方式で厳密に再読・再利用し、混在時に昇格させない。
- **分担と停止：** Workerは関係手順・focused回帰・再実行、Rootは別の隔離親で原画像・実出力・位置QA・全変更組を直接照合。版・dirty・入力指紋・command/exit・期待／観測／未達をprivateに残し、所有する一時物・childのみ回収後、証拠を再読し元資料hash/mtime不変を確認する。`CONTRIBUTING.md` の影響選定・順序で関係テスト、専用helper、Ruff check/format・PDF環境tyを実行し、共通保存・共有fixtureなどに及ぶ場合はcore全pytest・正式PDF phaseへ拡大。最新差分でReviewを受け直す。安全に分離できない、必須権限・資料がない等なら試行済み／未確認・不足と再開条件を示してBLOCKED。保証・制約・仕様を変える必要がある場合はREPLAN_REQUIRED。期待を下げて続行しない。
