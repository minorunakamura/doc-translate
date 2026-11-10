# PDF／EPUBのoffline性能baselineとローカル探索（Issue #54／#55／#56／#57）

[Approved Plan](../plans/pdf-epub-performance.md)のA／B／B2・第1段階のbaseline、第2段階の優先1（renderer関連付け索引）と優先2のpreparation操作内raw record memo／translation操作内catalogを扱います。Issue #55は現在入力のblock／unit／cell／node／target索引で探索を置き換え、schema・request方式・並列数・statusの共有検証経路は変更しません。同revisionのbefore／afterラベルは**再現性のcontrol比較**であり、高速化の証明ではありません。変更前後は別invocationの最初のbeforeラベル3回をprimaryとして固定します。Planにある旧revisionの秒数（特にB2のwarm Python process）は現在の測定に流用しません。preparation attempt探索の変更前後計測はIssue #56節、翻訳attempt-heavyの操作内catalogと前後比較は末尾のIssue #57節で扱います。ID分離・新並列、実provider／OCR／layout／model取得・意味品質・料金・任意規模の保証は本段階の対象外です。

## 新しい隔離状態からの再実行

repository rootをcwdとし、既存PDF extra付き`.venv`、pytest、Node、build済み`ai/dist`と同梱resourceを使います。追加install／sync／build・環境切替はしません。不足／hash不一致なら停止して報告します。実workspace・書籍・認証は渡しません。Nodeは既存`ai/tests/run-process.mjs`→実compiled helper→公開faux＋`no-network.mjs`です。HF offlineだけをnetwork遮断の根拠とはしません。

### 起動・操作

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/doc-translate resume --help  # 現行版には --json がある
.venv/bin/pytest -q tests/test_performance_verification.py
# 保存先は未作成のignore済みwork直下。各invocationに新しい名前を選ぶ。
.venv/bin/python .pi/skills/verify-project/scripts/performance_baseline.py \
  --evidence "work/issue54-render-$(date +%Y%m%dT%H%M%S)" --feature render --diagnostics
.venv/bin/python .pi/skills/verify-project/scripts/performance_baseline.py \
  --evidence "work/issue54-run-resume-$(date +%Y%m%dT%H%M%S)" --feature run-resume --diagnostics
```

他のtest／benchmarkと**非並行**でforeground実行します。`--feature all`も同じ固定recipeを逐次実行します。`--diagnostics`は通常の3回とは別の代表1回だけで、通常時間へ混ぜません。秒数閾値はpytestに入れません。

**実行前の停止予算**：各operation 120秒、EPUB成長200 sectionsだけ180秒、全invocation 1,800秒。停止時は所有process groupへSIGINT→45秒drain→必要時そのgroupだけSIGKILL・waitし、その試行を失敗として保存して後続を止めます。比較不能な失敗を成功の中央値へ混ぜません。入力は下記固定上限のみ、ネットワーク・実課金ゼロ。PDF35で成長を確認し350へ拡張しません。budget超過時に入力や期待値を緩めて成功扱いしません。

### 入力・利用者の入口・期待値

| 機能 | 自作入力と公開操作 | 期待／観測 |
| --- | --- | --- |
| render負荷A | PDF型／EPUB型それぞれN=50／200／500のbody paragraph、unit、block-targeted uncertainty。本文`Self-authored queue record {i}.`。PDF page1＋self-authored evidence、EPUB own.xhtml＋element_id。`SourceSnapshot.model_validate`→公開`save_source_snapshot`の**後**、公開`render_document`全体を計時 | N units・N原文uncertainties、未訳を追加したQA・source map・Markdown・summary等を保存。source-onlyの`partial`は期待結果でfailureではない。EPUB archive reader性能ではない |
| PDF B2 | 既存`test_pdf.conversion/local_models`＋`test_run.pdf_input`。200×100 pointの自作ページ、長辺1800px（1800×900）、所有2–7／2–36。35は同じtext／provenanceのpage10–36を追加しpage_count=36。公開converter／page backendのみ人工境界 | 実`cli.main`の外部Python processからrun→別fresh processで`resume --json`。completed、所有6／35、隣接参照は所有集合内だけ。追加reference権限なし、page1／9／37等を外部参照許可しない |
| EPUB B | `test_epub.make_epub`で単一XHTMLにN組の`<h1>Section {i}</h1><p>Original queue record.</p>`。2／20 sections、成長200 sections／400 units | 実CLI run→別fresh processのresume、completed、4／40／400 units |
| EPUB resource境界 | 最小2 sectionsを2つのspine XHTMLに分ける別case、1回 | archive reader→source→run／resumeでresource境界を維持。全規模×resource数の積は実行しない |

主要baselineは各before／after **3 raw値とmedian／min／max**。成長200 sectionsは代表3回だけ、複数resource境界とdiagnosticは各1回です。各操作はfresh Pythonでwarm filesystem cache（flushしない）。同じinvocation内の各EPUB規模は1つの生成archiveを使いinput hashを揃えます。既存fixtureのZIP timestampにより別invocationのarchive hashは変わるため、同一recipeだけを固定byte同一とせず、変更前後の固定hash受入れには同じ生成archiveを旧／新版rendererへ渡す対照を使います（#55の追加対照参照）。PDFのraw inputは固定人工byte、fixture定義・helperもhash記録します。renderは保存source hashを記録します。

各resumeで実faux logの**全byteが増えていない**こと、および既存originals／drafts／corrections／attempts／snapshots全fileのSHA256不変をassertします。completedと出力存在だけでなく、QA／source-mapのshape、unit数、PDF所有集合と実送信所有／参照も照合します。これは実翻訳の意味品質やviewerの保証ではありません。

## 時間・計数の定義

- `external_process_wall_seconds`：親からの起動、import／fixture／初期save、operation、stdout、終了・waitまで。EPUB archive生成は別の`input_generation_wall_seconds`。`setup_import_fixture_save_wall_seconds`はchild内のimport／人工PDFページ・model生成／render source validate+save。PDF operation内のPNG描画／copyはoperation時間に含まれます。interpreter起動時間だけは独立測定せずunknown（外側－内側を純startupとしない）。
- `operation_wall_seconds`：公開main全体、またはsave後の公開render全体。`operation_python_cpu_seconds`はそのPythonだけのprocess CPU、helper CPUはreaped childrenのuser+systemを別記。fixture準備をoperationへ混ぜず、外側end-to-endも残します。
- 保存`render_elapsed_seconds`はbuildだけでhash／write／pointer更新を含まないため、公開renderの外側timerの代用にしません。
- `--diagnostics`は別passのcProfile＋狭い委譲wrapper。`Path.read_bytes`のworkspace内call／返却byte／path別回数は**論理read**であり、open／buffer／mmap等を含む全readや物理disk I/Oではありません。hash／schema／domain／unit生成／load／stageは`diagnostic.json`の関数別calls・self／inclusive時間を読みます（Pydantic Rust内部を分解した検証件数ではない）。inclusive時間は重複し加算不可、worker thread内のprofileはunknownです。
- profileの`acquire_pdf`、`load_pdf_draft`、`assemble_pdf_source`、`prepare_document`、`translate_document`、`workspace_status`、`_build`、`render_document`を区別します。preparation `_call`はcollect／organize別のinclusive秒・回数も保存。補正／翻訳はそれぞれのmoduleの`_call`／validator／save関数から辿ります。resume事前整合もworkspace_status／load等のinclusive scopeであり、排他的stage秒の捏造はしません。
- translation／correction `_matches_request`だけは実returnを委譲して比較／match／nonmatchを数えます。preparationの`_read`候補読取りをmatch件数とは呼びません。詳細なmiss理由、reuse率の意味的分類、provider内部待ち／生成、正確なactive timelineはunknown。存在しないcounterを0と解釈しません。
- `os.replace`成功時の元file長とworkspace宛先からsnapshot／attempt／renders／pointer別write数・byteを計数。現実のatomic保存に委譲し、繰り返しwriteも数えます。物理write量・失敗前のtemporary write総量はunknown。保存後のsnapshot件数／attempt file sizeは在庫でありwrite総量ではありません。
- `requests.jsonl`は既存Node fauxが実際に受け取ったcontext。calls、phase、全message content中のtext UTF-8 bytes、画像数とdecoded base64 bytesを数えます。systemPrompt／protocol envelope・helper preflightはこのtext量の対象外。callsをunderlying attemptsと同一視せず、`saved-attempts.json`のattempt配列数と併記します。新規送信0はresume前後log byteで直接確認します。実provider token／throughputの推定はしません。
- `ru_maxrss`はmacOSではbytes（LinuxならKiB）。parentは**operation用Python process**の生涯peak（import／fixtureも含む）、childはそのPythonがwaitした子の最大peakで、Nodeだけの連続RSS samplingではありません。合算／同時aggregate RSSではなく、orchestrator RSS・provider内部・全threadのpeak合計はunknownです。

## 証拠・cleanup・再読

`manifest.json`にrevision、Python／Node／PDF依存、全helper resource一致hash、source／fixture／script hash、offline／worker／cache条件と事前budget、`dirty.txt`／`scope.diff`に差分を残します。各operationの`command.json`に**完全command・exit・外側wall**、`metrics.json`に内側timer／resource、`stdout.json`／`stderr.txt`、QA／map／summaryと日英Markdown・生成local assetsの全byte、synthetic attempts／requestを残します。`observations.json`と`statistics.json`がrawと集計です。入力・workspaceの一時絶対pathはcommand記録のみ、証拠fileはその外に保持します。

証拠は新規ignore済み`work/issue54-<unique>/`のみ。ignoreは秘密保護境界ではありません。自作text・人工認証だけを使い、実秘密・実書籍・不要raw logを取得／公開しません。所有TemporaryDirectoryに入力・人工model・credential・workspaceをまとめ、成功／失敗ともfinallyで削除し、所有child handleをwaitします。他process・lock・既存work・venvには触れません。`cleanup.txt`を確認し、cleanup後に全証拠fileをreadbackします。異常時は失敗commandと残存を別記し、古い成功を再利用しません。

[検証Skill](../../.pi/skills/verify-project/SKILL.md)／[Feature Map](../../.pi/skills/verify-project/references/feature-map.md#形式別runresume性能とrender負荷issue-54)から今回の機能だけを選びます。project trust確認後、新セッションまたは`/reload`、`/skill:verify-project performance`かSKILL.md明示readで再利用します。通常pytest・正式チェック・Rootの独立操作・Reviewerの受入れを代用しません。

## baselineの観測（Issue #54）

今回の計測はHEAD `60f94dff8c21c21dec7002efbbeddf842d6aecc5`、macOS arm64／Python 3.14.9／Node v24.22.1／Docling 2.142.1／docling-core 2.104.0／pydantic 2.14.1。production差分なし。helper全10fileのrepo／installed resource SHA256一致を各manifestへ記録（main.js `2527d74349df262364360316caff28eba18714a32786e8723c62de92d4e16135`）。historical Plan値は不使用。次はoperation-only秒、各raw3回のcontrol比較。

| 入力／操作 | before raw秒 | median [min, max] | after raw秒 | median [min, max] |
| --- | --- | --- | --- | --- |
| render-pdf-50-render | 0.033930 / 0.033691 / 0.033870 | 0.033870 [0.033691, 0.033930] | 0.033233 / 0.033345 / 0.033288 | 0.033288 [0.033233, 0.033345] |
| render-pdf-200-render | 0.703716 / 0.702781 / 0.710364 | 0.703716 [0.702781, 0.710364] | 0.701606 / 0.775218 / 0.707663 | 0.707663 [0.701606, 0.775218] |
| render-pdf-500-render | 10.773617 / 10.572198 / 10.632065 | 10.632065 [10.572198, 10.773617] | 10.601582 / 11.692266 / 10.638120 | 10.638120 [10.601582, 11.692266] |
| render-epub-50-render | 0.032842 / 0.034092 / 0.032176 | 0.032842 [0.032176, 0.034092] | 0.033030 / 0.033195 / 0.033503 | 0.033195 [0.033030, 0.033503] |
| render-epub-200-render | 0.765574 / 0.699607 / 0.700056 | 0.700056 [0.699607, 0.765574] | 0.703580 / 0.700592 / 0.697688 | 0.700592 [0.697688, 0.703580] |
| render-epub-500-render | 10.554061 / 10.601507 / 10.583159 | 10.583159 [10.554061, 10.601507] | 10.614594 / 10.717527 / 10.570514 | 10.614594 [10.570514, 10.717527] |
| run-resume-pdf-6-run | 1.247282 / 0.992460 / 0.975935 | 0.992460 [0.975935, 1.247282] | 0.988310 / 0.979025 / 0.982708 | 0.982708 [0.979025, 0.988310] |
| run-resume-pdf-6-resume | 0.215356 / 0.216634 / 0.266037 | 0.216634 [0.215356, 0.266037] | 0.215791 / 0.215049 / 0.214518 | 0.215049 [0.214518, 0.215791] |
| run-resume-pdf-35-run | 3.452680 / 3.541178 / 3.558094 | 3.541178 [3.452680, 3.558094] | 3.480490 / 3.524744 / 3.409215 | 3.480490 [3.409215, 3.524744] |
| run-resume-pdf-35-resume | 0.927315 / 0.860228 / 0.854856 | 0.860228 [0.854856, 0.927315] | 0.854520 / 0.854905 / 0.922019 | 0.854905 [0.854520, 0.922019] |
| run-resume-epub-2-run | 0.619264 / 0.621817 / 0.624095 | 0.621817 [0.619264, 0.624095] | 0.617181 / 0.620143 / 0.619777 | 0.619777 [0.617181, 0.620143] |
| run-resume-epub-2-resume | 0.086056 / 0.086155 / 0.088214 | 0.086155 [0.086056, 0.088214] | 0.088257 / 0.088263 / 0.085492 | 0.088257 [0.085492, 0.088263] |
| run-resume-epub-20-run | 4.339145 / 4.338113 / 4.356258 | 4.339145 [4.338113, 4.356258] | 4.338851 / 4.349287 / 4.374817 | 4.349287 [4.338851, 4.374817] |
| run-resume-epub-20-resume | 0.440333 / 0.433129 / 0.436573 | 0.436573 [0.433129, 0.440333] | 0.437898 / 0.434595 / 0.437626 | 0.437626 [0.434595, 0.437898] |

成長EPUB200 sections／400 unitsは別の代表3回（before／afterの全組合せへ増やさない）：
- run-resume-epub-200-run-growth: raw 50.704678 / 50.418620 / 51.053989秒、median 50.704678 [50.418620, 51.053989]秒。
- run-resume-epub-200-resume-growth: raw 7.208238 / 6.982967 / 7.185055秒、median 7.185055 [6.982967, 7.208238]秒。

最小複数resourceは2 XHTML／4 units、run→resume各1回でcompleted、calls5→追加0・旧immutable不変。全98 operations（render38、run/resume60、うち別diagnostic6）で期待値照合、各fresh processをwait、所有一時物cleanupと証拠readback完了。予算超過・measurement失敗なし。Createは共通Create／pstackをreadし、既存Skill／Mapへ追加した上記手順そのものを新規隔離状態で両機能実行した。将来ID／並列機能は未追加・未確認。

代表normal runの範囲（各baseline6回、growth3回）。CPUはPythonのみ、setupはimport／fixture等、outerはprocess end-to-end。

| run | Python CPU秒 min–max | setup秒 min–max | outer秒 min–max | calls／underlying attempts（在庫） | 送信text bytes／decoded image bytes |
| --- | --- | --- | --- | --- | --- |
| pdf6 | 0.333379–0.342909 | 2.999764–4.640824 | 4.587871–6.523057 | 5／5 | 36006／54688 |
| pdf35 | 2.141214–2.229765 | 3.031936–3.110189 | 7.094782–7.230252 | 10／10 | 140317／321292 |
| epub2 | 0.086542–0.090089 | 0.202001–0.251977 | 0.915257–0.971896 | 5／5 | 6772／0 |
| epub20 | 0.593001–0.618708 | 0.203176–0.230056 | 4.639435–4.680331 | 41／41 | 64484／0 |
| epub200 | 16.517592–16.619834 | 0.204609–0.209381 | 50.728928–51.365486 | 401／401 | 643224／0 |

別diagnosticの代表計数（通常秒数へ混ぜない）：

| scope | Path.read_bytes calls／logical bytes | SHA256 calls | Pydantic validate_python／validate_json calls | snapshot successful replace数／bytes | renders successful replace数／bytes |
| --- | --- | --- | --- | --- | --- |
| PDF35 run | 108／3,309,718 | 1,405 | 1,823／27 | 3／294,002 | 6／477,219 |
| PDF35 resume | 173／7,958,527 | 2,041 | 2,804／45 | 未観測counter（immutable追加なし） | 6／477,219 |
| EPUB20 run | 893／3,773,134 | 1,747 | 3,951／10 | 22／847,324 | 6／416,665 |
| EPUB20 resume | 1,002／4,485,980 | 2,763 | 6,563／26 | 未観測counter（immutable追加なし） | 6／416,665 |
| PDF型render500 | 4／226,798 | 1,014 | 1,003／3 | operation計測前save（対象外） | 6／1,505,276 |
| EPUB型render500 | 4／215,800 | 1,014 | 1,003／3 | operation計測前save（対象外） | 6／1,459,762 |

read／write wrapperはoperation各threadを含むが、profile列はmain threadのみ（worker thread内はunknown）。SHA256はprofileの`_hashlib.openssl_sha256`呼出し件数、schema列はPydantic core公開entry呼出し件数（全field内部検証数ではない）。domainの具体例：preparation `_validate_collection`はPDF35 run／resume 6／13回、EPUB20 120／260回、`_validate_organization`は各6／13回。translation `_validate`はPDF35 3／6回、EPUB20 60／120回。これ以外のdomain validatorもprofileに関数名付きで残り、単一の「全domain検証数」はunknown。

実candidate比較（comparison／match／nonmatch）はPDF35 runでcorrection21／0／21、translation2／2／0、PDF35 resumeはtranslation6／6／0、EPUB20 runはtranslation211／40／171、resume120／120／0。これは比較の反復件数でunique候補数／AI再利用率ではない。preparation `_read`はEPUB20 run326／resume511回だがmatch数ではない。詳細miss理由は未取得。

inclusive工程秒の例：PDF35 runはacquire .254487、correction7 calls合計2.439407、assembly .335347、preparation .302678（collect .145508／organize .137333）、translation .283712、render .110895（build .105908）。PDF35 resumeは事前workspace_status .111047、assembly .307981、preparation .041570、translation .090135、render .108089。EPUB20 runはpreparation2.789833（collect2.612116／organize .135446）、translation1.607821、render .171870。resumeは事前workspace_status .175364、preparation .162630、translation .173670、render .167019。これらは重複scopeを含み、排他的stage合計にはしない。render500 diagnostic全体はPDF25.270636／EPUB25.365172秒、通常約10.6秒と混ぜない。

normal runのparent生涯peakはPDF6約439–443 MiB／PDF35約445–447 MiB、EPUB2約60 MiB／20約62–63 MiB／200約100–101 MiB、reaped helper最大peakは約79–82 MiB。各raw byteはmetricsに保存。resumeでchild ru_maxrss=0はこのfresh processにwait済みhelperがないというresource観測であり、未知のaggregate RSSやprovider内部メモリを0と推定したものではない。

ローカル証拠（追跡外、別checkoutでは再取得）：`work/issue54-worker-render-20260717a/`、`work/issue54-worker-run-resume-20260717a/`。各`statistics.json`／`observations.json`→case/operationのcommand／metrics／diagnostic・saved QA／map／summary／attemptsを参照。`requests`項目は**logの累積在庫**でありresumeの追加分ではない（追加は`resume_appended_actual_requests=0`とbyte比較）。送信textはsystemPromptを含まないmessage content量。物理I/O、厳密なmiss理由、active／provider内計算・待ちtimeline、全thread profile、同時aggregate RSS、純interpreter startupはunknownのまま。

計測後、driverの手動取消にも45秒drainを適用する最小修正とinvocation残予算の適用を加えた。通常operation／fixture／production codeは不変、元計測版hashはmanifestへ保持し、通常値を新hashで測定したと偽らない。修正した取消境界はtimeout／KeyboardInterruptの2 selfcheckでowned groupだけSIGINT→45秒→SIGKILL→waitを確認した。自動回帰7件、既存consumer代表8件成功。初期selfcheckではsource-only QAに未訳のuncertaintyも追加されるため期待数を誤り2件失敗（製品変更なし、原文uncertainty Nと未訳Nを別照合へ修正）、初期Ruffでcheck引数不足1件を修正。これらをmeasurement成功値へ混ぜず、`work/issue54-worker-validation-20260717a/`に別記した。Rootの正式チェック／独立操作とReviewer受入れは別途必要。

正式型検査後、fixture importを既存の`import_module`方式へ合わせ、Optionalのassert、phase秒のfloat辞書、raw pstatsのruntime属性取得、test loaderのassertだけを補正した。profileの関数別tuple・call数・時間精度・JSON shapeは維持する（`get_stats_profile`の名前集約／丸めへ切り替えていない）。元98操作のhash／raw値は当時の証拠のまま、新helper hashによる再測定値とは扱わない。補正後は`work/issue54-worker-types-lVda8W/`にPDF6／EPUB2のrun→resumeと両型render50の別fresh-process diagnostic計6操作、selfcheck7件・PDF環境ty／Ruff成功、cleanup／readbackを記録した。これは小規模の接続・型修正確認であり、baselineの置換や高速化比較ではない。

## renderer索引の比較（Issue #55）

変更前はHEAD `8f19b4b`のclean状態でRootが取得した`work/issue55-root-before/`。変更後は同環境・同fixture・helper一致hash・fresh Python／warm filesystem・offline・非並行で`work/issue55-worker-after/`（all＋別diagnostic）を取得しました。最終的に未使用の`self.units`参照だけを削除したため、renderは最終差分で`work/issue55-worker-final-render/`を別invocationで再測定し、**この最終render値**を採用します。run／resume表はall測定時（探索ロジックは最終版と同じ）の値です。revision・実行時source／script hash・差分・完全command／exit／CPU／メモリは各manifest／operationへ保持し、後の文書更新まで当時測定したと扱いません。

同revision内の後半afterラベル3回はcontrolとして分離し、前後どちらも最初のbeforeラベル3回をprimaryに固定しました。有利なrepeat・controlを選びません。次は公開操作全体のoperation秒（setup／importは除外、renderのhash／write／pointer確定は含む）。通常値と別diagnosticを混ぜません。

### 公開render全体

| 入力／操作 | before raw秒 | median [min, max] | after raw秒 | median [min, max] |
| --- | --- | --- | --- | --- |
| render-pdf-50-render | 0.034319 / 0.031780 / 0.033369 | 0.033369 [0.031780, 0.034319] | 0.021344 / 0.021599 / 0.020964 | 0.021344 [0.020964, 0.021599] |
| render-pdf-200-render | 0.687721 / 0.699808 / 0.698979 | 0.698979 [0.687721, 0.699808] | 0.039162 / 0.041116 / 0.041740 | 0.041116 [0.039162, 0.041740] |
| render-pdf-500-render | 10.434544 / 10.632701 / 10.277307 | 10.434544 [10.277307, 10.632701] | 0.086077 / 0.085882 / 0.089203 | 0.086077 [0.085882, 0.089203] |
| render-epub-50-render | 0.031317 / 0.030137 / 0.031171 | 0.031171 [0.030137, 0.031317] | 0.023321 / 0.024182 / 0.022884 | 0.023321 [0.022884, 0.024182] |
| render-epub-200-render | 0.684464 / 0.678290 / 0.692289 | 0.684464 [0.678290, 0.692289] | 0.039177 / 0.038168 / 0.038854 | 0.038854 [0.038168, 0.039177] |
| render-epub-500-render | 10.192784 / 10.241180 / 10.229796 | 10.229796 [10.192784, 10.241180] | 0.081208 / 0.083164 / 0.083140 | 0.083140 [0.081208, 0.083164] |

### 代表run／無変更resume

次の初回EPUB値は同一recipeですが、既存`make_epub`のZIP timestampが変わり、invocation間のarchive SHA256は異なります。原本はcleanup済みのため旧ZIP全entry byte同一とは主張せず、**EPUB4操作は参考比較**とし、性能受入れは後述の同一archive追加対照で独立判定します。PDF入力とrender source hashは前後一致です。

| 入力／操作 | before raw秒 | median [min, max] | after raw秒 | median [min, max] |
| --- | --- | --- | --- | --- |
| run-resume-pdf-6-run | 1.091975 / 0.889975 / 0.889183 | 0.889975 [0.889183, 1.091975] | 1.107088 / 0.904163 / 0.984621 | 0.984621 [0.904163, 1.107088] |
| run-resume-pdf-6-resume | 0.193780 / 0.191297 / 0.199557 | 0.193780 [0.191297, 0.199557] | 0.194225 / 0.208610 / 0.209377 | 0.208610 [0.194225, 0.209377] |
| run-resume-pdf-35-run | 3.187953 / 3.201332 / 3.276034 | 3.201332 [3.187953, 3.276034] | 3.574072 / 3.282215 / 3.278976 | 3.282215 [3.278976, 3.574072] |
| run-resume-pdf-35-resume | 0.785303 / 0.793520 / 0.787226 | 0.787226 [0.785303, 0.793520] | 0.799814 / 0.781913 / 0.784422 | 0.784422 [0.781913, 0.799814] |
| run-resume-epub-2-run | 0.597463 / 0.582974 / 0.581933 | 0.582974 [0.581933, 0.597463] | 0.581790 / 0.583113 / 0.577846 | 0.581790 [0.577846, 0.583113] |
| run-resume-epub-2-resume | 0.092311 / 0.079330 / 0.076878 | 0.079330 [0.076878, 0.092311] | 0.077929 / 0.080718 / 0.081473 | 0.080718 [0.077929, 0.081473] |
| run-resume-epub-20-run | 4.033086 / 4.041388 / 4.025311 | 4.033086 [4.025311, 4.041388] | 4.524534 / 4.235985 / 4.012114 | 4.235985 [4.012114, 4.524534] |
| run-resume-epub-20-resume | 0.406692 / 0.400282 / 0.396398 | 0.400282 [0.396398, 0.406692] | 0.455177 / 0.398721 / 0.398593 | 0.398721 [0.398593, 0.455177] |

N500のafter／beforeはPDF **0.825%**、EPUB **0.813%**で、両型とも50%以下。medianの増加率（N200／N50、N500／N200）はPDF before **20.947倍／14.928倍**→after **1.926倍／2.094倍**、EPUB before **21.958倍／14.946倍**→after **1.666倍／2.140倍**。計算量の支配箇所を除いた人工source-only入力の結果であり、実書籍全体やEPUB archive読込みの高速化率ではありません。

代表run／resumeの許容上限は`max(before median × 1.10, before median + .05秒)`。**初回PDF6 runだけは上限 .978973秒に対し .984621秒で .005648秒超過、初回判定は未達**でした。他7操作は上限内です。PDF6のafter raw .904–1.107秒と後半control median .900161秒には分散があり、Root承認のもとPlanの外乱時対称追加を**一度だけ**実施しました。成功選別・閾値変更はしていません。

### PDF6の承認済み対称追加

`work/issue55-worker-pdf6-additional/`に旧／最終rendererのbyteコピー・hashとone-off操作を保存。同じwrapperが双方のrendererをsetup／operation timerの前にloadし、同じ既存performance_operation・fixture・実CLI／helperを使ってbefore→afterを3組、run→別fresh process resumeで計12操作しました。旧版だけのload費用をoperationへ入れていません。人工PDF input hashは全12操作で一致、immutable／request増加0／QAを照合、所有一時物cleanupと証拠再読済みです。

| 追加操作 | before raw秒 | median [min, max] | after raw秒 | median [min, max] |
| --- | --- | --- | --- | --- |
| PDF6 run | 1.105905 / .942973 / .950716 | .950716 [.942973, 1.105905] | .960561 / .947206 / .918617 | .947206 [.918617, .960561] |
| PDF6 resume | .207486 / .199853 / .206696 | .206696 [.199853, .207486] | .209758 / .210160 / .207145 | .209758 [.207145, .210160] |

初回primary3回＋追加3回の**全6 raw**（上の2表、control混入なし）を合算すると、run before median **.946844** [.889183, 1.105905]→after **.953883** [.904163, 1.107088]秒、上限 **1.041529秒**以内。resume before **.199705** [.191297, .207486]→after **.208993** [.194225, .210160]秒、上限 **.249705秒**以内です。追加だけ・合算とも合格ですが、初回未達の事実は取り消しません。起動／host変動を含む小差をrenderer単独の速度差と断定しません。

### EPUB2／20の同一archive追加対照

上のZIP timestamp差を確認後、Root承認により同一hash archiveを旧／最終rendererへ渡す追加対照を行いました。`work/issue55-worker-epub-fixed-evidence/`にinput hash・全command／exit・raw／resource・QA／map／日英本文を保存。各規模につきarchiveを一度だけ生成し、PDF6と同じ隔離module-load wrapperからbefore→afterを3組、公開run→fresh process resumeの計24操作を実行しました。旧primary値・controlは保持し、**異なるhashの初回値とは合算しません**。

| 固定archive操作 | before raw秒 | median [min, max] | after raw秒 | median [min, max] | 許容上限秒 |
| --- | --- | --- | --- | --- | --- |
| EPUB2 run | .577707 / .579765 / .575971 | .577707 [.575971, .579765] | .574631 / .567655 / .570291 | .570291 [.567655, .574631] | .635478 |
| EPUB2 resume | .077117 / .077142 / .078165 | .077142 [.077117, .078165] | .087294 / .077929 / .076982 | .077929 [.076982, .087294] | .127142 |
| EPUB20 run | 3.978144 / 3.964201 / 3.964383 | 3.964383 [3.964201, 3.978144] | 3.982247 / 3.965720 / 3.986915 | 3.982247 [3.965720, 3.986915] | 4.360821 |
| EPUB20 resume | .398732 / .393452 / .397389 | .397389 [.393452, .398732] | .414407 / .391448 / .412655 | .412655 [.391448, .414407] | .447389 |

4操作とも同条件before上限内。全24操作でcompleted・unit4／40・request5／41・QA checks passed、全resumeでrequest log増加0・旧immutable hash不変・cleanup／readback成功。さらに全12組の旧／新版run／resumeで日英Markdown・QA・source map・assetsの**全byte一致**を確認しました（`equivalence.json`、elapsed／派生directoryを含むsummaryだけ分離）。初回source-only characterizationとは別に、実準備／翻訳を経た出力の同等性です。

追加対照のRoot再実行入口（既存PDF extra／helper・offline条件、未作成のwork直下を指定。workのone-off資産を保持する同checkout用）：

```sh
.venv/bin/python work/issue55-worker-pdf6-additional/measure.py "work/issue55-pdf6-replay-$(date +%Y%m%dT%H%M%S)"
.venv/bin/python work/issue55-worker-epub-fixed/measure.py "work/issue55-epub-replay-$(date +%Y%m%dT%H%M%S)"
```

これらは固定PDF6／EPUB2・20だけの今回の証拠補完です。新しい汎用runner／公開test APIではなく、再取得の標準入口は引き続き既存performance_baselineです。

### control・資源・同等性と限界

後半controlのmedian秒をprimaryと分離して記します（raw各3回とmin/maxは各`statistics.json`の`-after`キー）。renderはN50／200／500順、run／resumeは各組の順です。

| control | before invocation | after invocation |
| --- | --- | --- |
| PDF render | .032098 / .688987 / 10.103604 | .022041 / .040330 / .085963（最終render） |
| EPUB render | .031305 / .677072 / 10.221784 | .021990 / .038367 / .081418（最終render） |
| PDF6 run／resume | .893105 / .194331 | .900161 / .195598（all） |
| PDF35 run／resume | 3.217650 / .790516 | 3.430520 / .853003（all） |
| EPUB2 run／resume | .581677 / .080454 | .582132 / .078295（all） |
| EPUB20 run／resume | 4.050972 / .400488 | 3.998541 / .394076（all） |

資源はall invocationのprimary normal run範囲。PDF6／35のPython CPUは .316091–.317467／2.066581–2.182081秒、parent生涯peakは約439／444–446 MiB。EPUB2／20／成長200はCPU .078651–.080715／.561438–.633757／15.741473–16.976080秒、parent約60／62–63／99–102 MiB。reaped child最大peakは約81–82 MiBで同時RSS合計ではありません。既存translation workers=2、preparation／correctionは逐次のまま。新索引は現在source／unitsのID・位置・object参照のみO(B+U)、画像や履歴snapshotの一括常駐・永続cacheは追加しません。aggregate RSSは未計測です。

同等性はproduction編集**前**に旧rendererで公開renderを実行・保存した`work/issue55-worker-characterization/`を対照に確認しました。同じ保存入力8ケース（PDF型／EPUB型各N50／200／500、PDF cell／list／caption／crop、EPUB attributes／table／link／unsafe外部image／local image）をfinal差分で再実行し、日英Markdown・source map・QA（全対象／順序／anchor含む）・生成assetsの**全byte一致**、入力file hash不変、QA checks全passedをassert。非決定的なelapsed秒とその派生render directoryを含むsummaryだけを直接byte比較から分離しました。比較したQAの対象を削っていません。外部送信0（renderは保存-only、runはnetwork拒否付きfaux）。

最終renderの通常36操作＋別diagnostic2操作、allの通常92操作＋別diagnostic6操作、追加PDF6の12操作はいずれもexit0・期待照合・所有child wait／一時物cleanup・全証拠readback済み。allの成長EPUB200と最小複数resourceも既存recipeのまま成功しましたが、新しい性能目標にはしません。恒久回帰は探索回数（uncertainty数増加で全block／unit再走査なし）、混合block／cell／unit targetの順序・重複除去、page review順、Markdown/assets保存を追加し、秒数依存assertは入れません。既存render全体とreview／run／resume／revise／statusのfocused consumerも成功。初期characterizationのdirectory作成漏れ2失敗と初期format差分は測定から分離して解消し、`work/issue55-worker-validation/`に記録しています。

Rootの最終正式チェック・別invocationでの直接確認とReviewer受入れは別途必要です。Issue #55の採用対象はrenderer索引だけで、attempt索引・ID分離・新並列・status軽量化／UIは追加していません。実provider・OCR／layout・意味品質・任意書籍・他OS／remote CIの性能は未確認、offline合格をそれらの採用推薦にしません。

## preparation保存attempt探索の比較（Issue #56）

本節の入口は公開`save_source_snapshot → prepare_document → load_preparation`だけです。翻訳・render・full pipelineを前提生成しません。production変更前のbeforeをRootが直接確認してから操作内raw record memoを実装し、同seed・同区間でafterを比較します。既存の形式別run／resume・render recipeは上のままです。

### 操作・期待値・固定区間

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest -q tests/test_performance_verification.py
BEFORE="work/issue56-worker-before-$(date +%Y%m%dT%H%M%S)"
.venv/bin/python .pi/skills/verify-project/scripts/preparation_baseline.py --evidence "$BEFORE"
# afterはproduction変更を別途承認・実装した段階だけ実行。同じ生成seedで比較する。
.venv/bin/python .pi/skills/verify-project/scripts/preparation_baseline.py \
  --evidence "work/issue56-worker-after-$(date +%Y%m%dT%H%M%S)" --seed "$BEFORE/seed"
```

repository root・既存PDF extra環境／Node／compiled helper全file hash一致が前提です。不足なら停止し、install／sync／buildしません。人工credential・既存`preparation-process.mjs`の実compiled Node／公開faux／no-networkだけを使います。実workspace・実認証・書籍・providerは使いません。`--size 2`は新規隔離状態での接続確認用で、性能代表は事前固定の**100 heading＋body group（200 units、101 saved attempts）**です。PDF型の公開snapshotであり、PDF取得／EPUB readerの計測ではありません。

生成sourceは`performance_operation.render_source`の自作paragraphを2件ずつheading／bodyへ変え、見出しlevel1でgroupを分けます。Issue #58以降の**新規seed**は見出しを`Section group0`等にし、unitのmarker保護後もgroupごとのwireが一意となる通常textを使います。旧`Section 0`等は数字が同じmarkerになり、v2の正常なreuseで独立attempt数を減らすためです。3-groupの公開create/load回帰でcollect3＋organize1の実requestと4元recordを確認し、標準100-group／101 attemptsのassertは緩めません。新旧recipeはmanifestのsource／script hashとseed内の原文で識別します。保持済みv1 seed・過去比較値・互換fixtureは変更せず、過去v1との比較は`--seed`で旧seedの同byteを使い、新規recipeの値を旧比較へ混ぜません。各collect回答はそのbody unitの実`source_text`と`queue`の引用根拠を持ち、organizeもその根拠語だけを採用します。`tests/test_preparation.py`の実`run`でseedを生成し、generate／save／raw・schema／domain validatorをfakeにしません。`seed/`は人工workspace全fileだけを保持（credential／answersは所有一時directoryからcleanup）。`seed-hashes.json`の全file SHA256と各コピーの全byteを検査します。afterは`--seed`で同じ生成物を独立コピーし、再生成のelapsed／attempt byte差を持ち込みません。永続production indexや全snapshot常駐ではありません。

3モードを順に各fresh Python process **通常3回＋別diagnostic1回**、他test／benchmarkと非並行、warm filesystem・flushなしで実行します。adoptedはpointer付き公開prepare、unadoptedはコピー後に準備pointerだけ除いた公開prepare（orphan保存成功の再利用）、loadはpointer付き独立公開loadです。各操作後、旧immutable全SHA256・seed全byte不変、prepareの採用digest一致、loadのrun数101と根拠語、実faux新request logが作られないことをassertします。`answers={}`なので意図しないmissは成功回答で隠さず失敗します。

**性能比較のprimary区間はunadoptedの`search_call_inclusive_seconds`**（collect100＋organize1の実`_call`委譲wrapper秒の合計）に事前固定します。全callが保存成功にhitし実送信ゼロなので、glob／到達候補read・hash／request・config・raw／schema／domain照合を含む探索proxyです。純file探索だけではなく、emit等も含みます。生成が発生した試行は比較不能として停止します。inclusive scopeは重複加算せず、public prepare全体の`operation_wall_seconds`、inclusiveな内部公開load、独立load、outer process wall、Python CPUと分けます。adoptedとunadoptedの差をそのまま排他的load時間とは呼びません。通常timer wrapperの設置費用はoperation wallに含まれ、fixture import／seed生成／コピーはsetupまたは計測外です。afterで区間を変更しません。

合格判定は同seed・同helper／config・同cache／起動条件のprimary中央値がbeforeの70%以下。全体寄与が小さくてもread削減と公開prepare全体秒は別報告します。代表run／resumeの悪化上限`max(before×1.10, before＋.05秒)`は既存driverの別比較で、今回のprepareだけでは完了条件7を受け入れません。秒数閾値をpytestへ入れません。

### 観測・証拠・cleanup

`performance_operation.preparation_measurement`は通常passでは`_call`と公開`load_preparation`へtimerのみ委譲します。diagnosticは既存cProfile／logical read・atomic replace観測に加え、`_read`のattempt scope内で実`Path.read_bytes`と`hashlib.sha256`へ委譲し、record digest別のread／hashを`metrics.json:attempt_read_hash_counts`へ記録します。`search_call`（operation探索）／`public_load`（独立した全run再検証）／`operation_other`を分け、公開loadの正当な再読を探索の重複と数えません。この境界外の全read手法・全hash・物理I/Oを網羅するcounterではありません。profile／counterの遅い秒数は通常3回へ混ぜません。到達record数、合計read／hash、最大反復数、論理bytesを別集計し、将来も計数境界を無断変更しません。

親Pythonの生涯`ru_maxrss`（macOS bytes、MiBへ換算）にはimportも含み、Node seed生成のpeakは通常reuse操作のメモリではありません。reuse子のreaped-child peak=0は新helperを起動しなかったという観測です。同時aggregate RSS／物理I/O／cache residency／実provider性能はunknownです。record保持によるメモリ増と探索短縮を別報告します。

各child120秒、invocation900秒の残予算を次child起動前に検査します（seed生成は既存generateの有限timeout／retryに従い、900秒の強制interrupt timerではありません）。超過時は後続を止めます。childのtimeout／取消は既存`invoke`の所有groupだけSIGINT→45秒drain→必要時SIGKILL／waitです。所有TemporaryDirectoryの入力／answers／credential／コピーworkspaceだけfinally削除し、seedと証拠は残します。他process・既存workspace・lock・venv・work全体は触りません。

証拠は新規ignore済み`work/issue56-worker-<unique>/`。manifestのrevision／source・fixture・helper・script hash／条件、dirty／diff、verification-sourcesの新helper全byte、seed全byte／hash、実seed request（自作のみ）、各command／exit／stdout／stderr／metrics／別diagnostic、observations／statistics／cleanupを保存します。参照はそのdirectoryから解決し、cleanup後に全fileをreadbackします。`--seed`使用時は元directoryのseed hash表を先に照合し、変更済みseedは拒否します。秘密値・書籍・不要raw logは残しません。Skill／Mapは手順・期待だけを保持し、今回の成功履歴へ変えません。正式チェック・Rootの独立公開操作とReviewer gateは別に必要です。

### production変更前の観測（Issue #56）

HEAD `637935f5a1f172d7c3cfdf4d0970bb93ca566d62`、production差分なし。macOS arm64／Python 3.14.9／Node v24.22.1／pydantic 2.14.1、既存helper全10file hash一致（main.js `2527d74349df262364360316caff28eba18714a32786e8723c62de92d4e16135`）。source snapshot SHA256は`0d7c6155efff84da8ad5158b2aa87707a2ba862e96c5af23adb586c764ce345f`、全107file／576,922 bytesのseed hash表SHA256は`9796b6ded8edf6b176f49d0eb339deb0f75d86d0d8c15187141774e477816b8d`。実seed prepareは13.231720秒（通常reuse時間から除外）。以下は通常各3回、単位秒、diagnosticは混ぜていません。

| mode／区間 | raw秒 | median [min, max] |
| --- | --- | --- |
| unadopted **primary探索proxy** | .816838 / .816953 / .855215 | **.816953** [.816838, .855215] |
| unadopted 公開prepare全体 | .877837 / .877979 / .916819 | .877979 [.877837, .916819] |
| adopted 探索proxy | .806467 / .812116 / .841849 | .812116 [.806467, .841849] |
| adopted 公開prepare全体 | .921873 / .931672 / .960553 | .931672 [.921873, .960553] |
| adopted 内部公開load（inclusive） | .054619 / .058356 / .057104 | .057104 [.054619, .058356] |
| 独立公開load全体 | .055567 / .056287 / .057748 | .056287 [.055567, .057748] |

primary探索の全体比は各試行 **93.051%／93.049%／93.281%**（adoptedは87.481%／87.167%／87.642%）で、この100-group workloadでは探索proxyが支配的です。区間を維持したafterのprimary中央値上限は **.571867秒**（before×.70）。このbefore取得段階ではafter／production最適化は未実施でした。以下のafter比較と区別し、before取得だけで70%目標や完了条件3／4／7／10全体の達成を主張しません。

別diagnosticはadopted／unadoptedとも探索read **5,151回**・hash **5,151回**、unique101 record、同record最大101回、論理read **23,100,131 bytes**。辞書順の各成功候補まで止まる走査が全101 callで繰り返される観測です。adopted内部の公開loadは別に各101 read／hash・unique101・最大1回・460,835 bytes、unadopted内部loadは0回。独立loadも各101 read／hash・最大1回・460,835 bytesで、探索の重複には算入していません。diagnostic profileにはschema／domain等の実関数呼出しも保持しますが、Pydantic全field内部検証や物理I/Oの総件数には換算しません。

通常Python CPU rawはunadopted .876562／.876374／.915195、adopted .920541／.930267／.958987、独立load .055533／.056219／.057607秒。outer process wall rawはそれぞれ1.167891／1.169326／1.213506、1.281050／1.221013／1.251088、.348674／.349477／.347909秒。親生涯peak RSS rawはunadopted63.593750／63.015625／63.125000、adopted64.031250／64.234375／64.140625、独立load63.234375／63.328125／63.156250 MiB。全reuse操作のreaped-child peakは0（新helper起動なし）。seed生成時のNode peakと同時aggregate RSSは未計測です。

証拠（追跡外、同checkout）：`work/issue56-worker-before/`のmanifest／seed-hashes／seed、各modeのcommand／metrics／別diagnostic、observations／statistics／contribution／cleanup。全12 fresh operationsはexit0、追加request0・旧immutable／seed全byte不変を照合し、所有一時物cleanup後に全fileをreadbackしました。`work/issue56-worker-connection/`と`work/issue56-worker-seed-replay/`は`--size 2`による新規生成と`--seed`再実行の各12操作で、seed hash表全byte一致を照合した接続確認であり、性能primaryへ混ぜません。

focused検証は`work/issue56-worker-validation/`：新selfcheck代表単独1件、同module10件、既存preparation全61件（repair上限・raw/schema/domain・method/config・orphan・旧採用等）、変更Python3fileのRuff check／format／tyが成功。初期selfcheck2件はheading数字がunitの保護markerになることを見落とした期待誤りで失敗し、公開unitのheading block対応を照合するよう修正（production変更なし）。初期import順／型検査の指摘も通常計測前に解消し、失敗記録は成功時間へ混ぜていません。このbefore取得時点ではRootの正式チェック・独立公開操作・Reviewer gate、および実provider／実workspace／任意書籍／他OSの評価は未実施でした。Rootによる後続のbefore直接確認とafter段階は次節へ分離します。

### 操作内raw record memoのafter（Issue #56）

Rootがbefore helper・raw／stat・全seed hashを直接確認し、同seedの別invocation `work/issue56-root-before/`で公開prepare／load12操作を確認した後に実装しました。production差分は`preparation.py`だけです。`prepare_document`内の辞書へ、初回到達して`_read`が成功したraw recordと、新規atomic保存成功後の正規JSON recordを登録します。各callのsorted glob・元request／規定repair／方式／config／raw回答／根拠検証は残しました。追加fingerprint indexや回答の受理結果cacheは不要だったため作っていません。公開API／schema／request／config／Node／translationは不変です。

afterは`work/issue56-worker-after/`。上のworker beforeのseed全107fileを`--seed`で再使用し、Root beforeともseed hash表の全byte一致、Python／Node／helper／計測script hash・条件／区間／反復数一致を確認しました。source SHA256と差分は各manifest／scope.diff、比較計算はafterの`comparison.json`に保持します。次は通常3回のみで、別diagnosticの時間は混ぜません。

| mode／区間 | after raw秒 | median [min, max] |
| --- | --- | --- |
| unadopted **primary探索proxy** | .126623 / .124110 / .125328 | **.125328** [.124110, .126623] |
| unadopted 公開prepare全体 | .186525 / .183350 / .184729 | .184729 [.183350, .186525] |
| adopted 探索proxy | .118077 / .119552 / .120540 | .119552 [.118077, .120540] |
| adopted 公開prepare全体 | .237548 / .236169 / .236786 | .236786 [.236169, .237548] |
| adopted 内部公開load（inclusive） | .058673 / .057023 / .055881 | .057023 [.055881, .058673] |
| 独立公開load全体 | .058706 / .061683 / .058341 | .058706 [.058341, .061683] |

primary中央値はworker before .816953秒の **15.341%**（上限 .571867秒を満たす）、Root before .851112秒の **14.725%**です。Root primary rawは .851112／.873658／.848776秒、median .851112 [.848776, .873658]。公開prepare全体中央値の比はworker beforeに対し **21.040%**、Root before .915644 [.912372, .937235]秒に対し **20.175%**。afterの各試行の探索proxy寄与は **67.885%／67.690%／67.844%**（adoptedでは49.707%／50.621%／50.907%）です。純I/O秒や実書籍全体の速度差へ換算せず、全体短縮と探索短縮を区別します。独立loadは最適化しておらず、全run再検証を残したcontrolです。

別diagnosticの探索read／hashはadopted／unadoptedとも各 **101回・unique101・最大1回**（before各5,151回・最大101回）、論理bytes **460,835**（before23,100,131）。adopted内部の公開loadは別に各101回・最大1回・460,835 bytes、独立loadも同じで、正当な再読を削減したとは主張しません。小さい初回／repair回帰では、新規保存recordを後続探索で元fileからread／hashする回数が0であることも確認しました。別prepare／公開loadでの再readと改ざん拒否、辞書順の先勝ち、未到達の後方破損を非read・前方到達破損を拒否し旧pointerを保持することを公開prepare／loadから確認しています。

| mode | after Python CPU raw秒 | after parent生涯peak RSS raw MiB | after outer process wall median [min, max]秒 |
| --- | --- | --- | --- |
| unadopted | .185724 / .182584 / .184442 | 65.265625 / 65.265625 / 65.078125 | .488423 [.486833, .503050] |
| adopted | .235944 / .235393 / .235927 | 67.390625 / 65.265625 / 65.187500 | .537393 [.534309, .584972] |
| 独立load | .058598 / .061502 / .058190 | 63.578125 / 63.296875 / 63.531250 | .365228 [.362951, .370202] |

primary親peakのmedianはbefore63.125000→after65.265625 MiB（観測差+2.140625 MiB）。このworkloadでは到達した101 raw recordをoperation内に保持し、対応する元fileのserialized在庫量は460,835 bytesです。これはPython dictの実メモリbyteやRSS増分の因果配賦ではありません。保持量は到達record＋今回保存recordに比例し、全履歴snapshot／PNG・mutable pointer・別operationへのcacheは追加しません。helper新起動はなくreaped-child peakは全0、物理I/O／同時aggregate RSS／dict単独のRSS寄与はunknownです。

after12 fresh操作のexit0・追加request0・旧immutable／seed全byte不変・所有一時物cleanupと証拠全fileのreadbackを確認しました。更新手順そのものの新規隔離実行は`work/issue56-worker-after-create/`の`--size 2`（実Nodeからseed新規生成→12公開操作）で別に成功し、primaryへ混ぜていません。`work/issue56-worker-optimization-checks/`には追加catalog回帰の代表単独1件、preparation64件＋selfcheck10件＝74 passed、公開consumer代表5件、変更Pythonと既存helperのRuff／format／ty成功を保持します。初期の新テストに対するRuffのnested-with指摘1件は統合して解消し、失敗を性能値へ混ぜていません。

Rootが取得した代表PDF35／固定EPUB20のrun／無変更resume beforeは`work/issue56-root-cli-before/`（median秒：PDF run3.505303／resume .862172、EPUB run4.322620／resume .443209）。完了条件7の同条件after・全正式チェック・別invocationでの独立公開確認・Reviewer gateはRoot側の後続確認であり、今回のprepare探索合格で代替しません。実provider／実workspace／OCR／任意書籍／他OSの性能と意味品質は未確認です。

以上はWorker段階の記録で、その後Rootが独立after・正式確認を実施しました。同条件・通常各3回（準備のdiagnosticは別pass）の中央値は、primary探索 .851112→.115456秒（beforeの13.5653%）、公開prepare全体 .915644→.171691秒（18.7508%）、親peak RSS 63.28125→65.4375 MiB。代表PDF35はrun 3.505303→3.444379秒／resume .862172→.798275秒、同byte固定EPUB20はrun 4.322620→4.038545秒／resume .443209→.365403秒で、4操作とも`max(before×1.10, before＋.05秒)`以内です。再利用操作の追加request0・旧byte不変と、公開直接操作による新規record登録・別operation再検証・到達／未到達破損境界も確認しました。正式チェックはcore 920 passed／Doclingなしによる123 skipped、PDF4ファイル181 passed、Ruff check／format・ty・公開import・CLI help・diff checkは全exit0・警告なし。集計と全raw／stat・直接操作への導線は`work/issue56-root-checks/acceptance-results.json`、正式command／exitと各logは同directoryの`repository-checks.json`を参照し、詳細は`work/issue56-root-{before,after,cli-before,cli-after,direct}/`に保持しています。独立Correctness（16ee6f41）／Ponytail（93384d8a）Reviewはともに指摘なしでしたが、CodeReviewは未実施であり、最終承認を示す記録ではありません。

## translation保存attempt探索の計測（Issue #57）

本節は公開`save_source_snapshot → prepare_document → translate_document → load_translation`による固定seed計測と、承認後の操作内catalog最適化を扱います。beforeと実装後の観測を区別します。既存preparation／renderer／run・resumeのrecipeは変更しません。

### 操作・期待値・固定区間

repository root、既存PDF extra付き`.venv`／pytest／Node、compiled `ai/dist`と同梱resource全file hash一致が前提です。不足なら停止し、install／sync／buildしません。人工credential・既存`preparation-process.mjs`／`translation-process.mjs`の実compiled Node→公開faux＋no-networkだけを使います。実workspace／書籍／認証／provider通信は使いません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest -q tests/test_performance_verification.py
# 新規ignore済みwork直下。他benchmark／testと非並行、foregroundで実行。
.venv/bin/python .pi/skills/verify-project/scripts/translation_baseline.py \
  --evidence work/issue57-worker-connection --size 2
.venv/bin/python .pi/skills/verify-project/scripts/translation_baseline.py \
  --evidence work/issue57-worker-before
# 承認された実装後、同じseed全byte・同script・同区間で別invocation。
.venv/bin/python .pi/skills/verify-project/scripts/translation_baseline.py \
  --evidence work/issue57-worker-after-new --seed work/issue57-worker-before/seed
```

`preparation_baseline.seed_source/create_seed/files`と既存`performance_baseline`のhash／分布／invoke／immutableを再利用します。新規seedのheadingは上記#56手順と同じ`Section groupN`で、v2でも独立collect100＋organize1を維持します。保持済み旧seedの`--seed`利用では再生成・変更せず、同byte比較を維持します。自作PDF型common sourceの**100 heading＋body group／200 units**、根拠付きprepare collect100＋organize1を実生成し、`tests/test_translation.py:translate`／既存CONFIGで**翻訳100 attempts**を生成します。`--size 2`は2 group／4 unitsの接続確認だけです。翻訳は既存concurrency=2のまま。unadopted回収ではrun列がsource順になりseedの並列完了順と異なり得るため、seedとの比較は全run集合とそれ以外のsnapshot全field一致、返却digestと実pointer一致を確認します。旧snapshotは保持し、新しい順序のsnapshot追加を許容します。生成／save／domain validatorをmockせず、全chunkの回答ID順と`訳:`＋実source_text、completed、run／chunk／unit数を公開loadで照合します。PDF取得・EPUB archive読込みや意味品質の性能試験ではありません。

人工workspaceの全fileを`seed/`と全SHA256表`seed-hashes.json`に保持し、各operationは全file hash一致のfreshコピーを使います。afterは`--seed BEFORE/seed`で再生成せず同byteをコピーします。既存hash表と異なるseedは拒否します。credential／回答設定は保持seedに含めません。adopted（pointer付き公開translate）、unadopted（**翻訳pointerだけ除去**して公開translate）、独立公開loadを各fresh Pythonで通常3回＋別diagnostic1回、warm filesystem・flushなしで逐次実行します。startup／import／seed生成／コピーはoperation timer外、外側process全体時間も別に残します。

primaryは**unadoptedの`pre_job_search_inclusive_seconds`**。`Path.glob('*.json')`が当該workspaceの`attempts/translation`に初めて呼ばれた時点から、`translation.ThreadPoolExecutor.__enter__`の冒頭までの委譲wrapperです。現コードのsaved_runs全read→orphan照合→残りgroupのjob化を含み、request再生成／raw・schema／domain検証／chunkの再確認・storeも含む**inclusive pre-job探索proxy**です。純file探索だけでも公開translate全体時間でもありません。previous公開loadはglobしないため区間外です。通常passにも同じ小さいtimer wrapperを付け、afterも同じ境界に固定します。境界が成立しない変更では止めてRootへ相談し、都合よく別指標へ変更しません。

各translateの探索区間1回（独立loadは0）、**worker submit 0**、実追加request log 0 byte、旧immutable／seed全byte不変、返却digestと実pointer一致・completedをassertします。fauxは`{}`でもmissを成功回答にできるので、completedだけではreuseの証拠にせずlogゼロが必須です。公開translate全体wall／Python CPU／親RSS、内部previous公開loadのinclusive時間、独立load、snapshot保存数／bytesは別測定。探索秒÷公開全体秒のraw寄与も残します。afterのprimary median目標はbeforeの70%以下です（結果は下記）。代表run／無変更resumeは既存driver等の別比較で`max(before×1.10, before＋.05秒)`以内を確認し、この翻訳専用計測だけで完了条件7を受け入れません。秒数assertをpytestに入れません。

### diagnostic・保持量・安全性

診断は別passで、実`_read`内の`Path.read_bytes`／`hashlib.sha256`／`json.loads`、実`_request`／`_matches_request`に委譲して件数を保存します。thread-local scopeにより`operation_search`／`public_load`／`store`／`operation_other`を分離し、公開loadやstoreの正当な再読をoperation探索の重複と偽りません。record digest別read／hash／parse、論理read bytes、attempt read外の探索中json.loads（projection等）、request／比較match・nonmatchを`attempt_diagnostic_counts`へ記録します。全schema内部検証数や物理I/O、網羅的miss理由には換算しません。diagnosticの遅い時間は通常統計に混ぜません。

`os.replace`成功に委譲し、翻訳snapshot本体の保存数／元file bytesを数えます（pointer更新は別、同じdigestの既存在庫は新規保存0）。在庫をwrite総量と呼びません。変更前saved_runs／変更後catalogはいずれもこの規模の全100 raw recordsを保持し、adoptedはprevious採用snapshotを1件loadしますが、全履歴snapshotは一括常駐させません。counterはdigest／件数だけを保持し、record自体を追加常駐させません。親`ru_maxrss`はoperation Pythonの生涯peak（macOS bytes、Linux KiB、import含む）。wait済みchild最大peakは同時aggregate RSSではなく、reuse操作の0は新helperを起動していないという観測だけです。seed生成Node peak・同時aggregate RSS・物理I/O・cache residency・実provider性能・純startupはunknownです。

各child120秒、invocation900秒の残予算を次child起動前に検査します。seed生成は既存generateの有限timeout／retryで、900秒のhard interruptではありません。超過なら後続を止め、失敗を成功時間へ混ぜません。既存invokeの所有groupだけSIGINT→45秒drain→必要時SIGKILL／wait、所有TemporaryDirectoryのコピーworkspace／人工credential／回答設定だけfinally削除します。他process・既存workspace・lock・venv・work全体を触りません。

証拠は新規ignore済みwork直下。manifestのrevision／source・fixture・script・helper hash／条件／unknown、dirty／diff、verification-sourcesのhelper全byte、seed全byte／hash、人工seed request、各command／exit／stdout／stderr／metrics／別diagnostic、observations／statistics／cleanupを保持します。秘密・書籍・不要raw logは残しません。cleanup後に保持seedを含む全fileをreadbackします。Skill／Mapは手順と期待を保持し、成功履歴は本節の観測と専用証拠へ分離します。Create成立やbefore取得だけで最適化／正式チェック／Root独立公開操作／Reviewer gateを代用しません。

### production変更前の観測（Issue #57）

HEAD `b69935f6d73581a8fb76aa4f3c8abf7237dcb670`、production差分なし。macOS arm64／Python 3.14.9／Node v24.22.1／pydantic 2.14.1、repoと同梱helper全10file hash一致。source snapshot SHA256は`0d7c6155efff84da8ad5158b2aa87707a2ba862e96c5af23adb586c764ce345f`、seed全408file／22,092,102 bytes、seed hash表SHA256は`c364972aea9efd2f7d8e86f9b758a892712e26cd910b3856421773792508f908`。翻訳attempt100 records／415,268 bytes（prepareは別に101 records）。実prepare＋translate seed生成19.148889秒は通常reuse時間から除外しました。

| mode／区間 | 通常raw秒（3回） | median [min, max] 秒 |
| --- | --- | --- |
| unadopted **primary pre-job探索proxy** | 2.266065 / 2.244978 / 2.264825 | **2.264825** [2.244978, 2.266065] |
| unadopted 公開translate全体 | 2.334741 / 2.312246 / 2.332923 | 2.332923 [2.312246, 2.334741] |
| adopted pre-job探索proxy | .080892 / .080937 / .079612 | .080892 [.079612, .080937] |
| adopted 公開translate全体 | .344570 / .346780 / .340099 | .344570 [.340099, .346780] |
| adopted 内部previous公開load（inclusive） | .187981 / .187878 / .184306 | .187878 [.184306, .187981] |
| 独立load操作全体（stdout serialize含む） | .186559 / .192086 / .183608 | .186559 [.183608, .192086] |
| 独立公開load wrapper（inclusive） | .185075 / .190519 / .182096 | .185075 [.182096, .190519] |

primary探索の全体寄与は各試行**97.0585%／97.0908%／97.0810%**。adoptedは23.4763%／23.3396%／23.4086%で、差を排他的load時間とは呼びません。afterの同境界primary中央値上限はbefore×.70＝**1.585378秒**。この段階ではafterもproduction最適化も未実施で、70%目標・完了条件3／4／7全体の合格を宣言しません。

別diagnosticのoperation探索はadopted／unadoptedとも元翻訳attemptのread／hash／parseが各**100回**、unique100、同record最大1回、論理read **415,268 bytes**。一方unadoptedの`_request`／`_matches_request`は各**5,050回**（match100／nonmatch4,950）、attempt read外の探索中json.loadsは5,350回です。adoptedのrequest／比較は各100回・match100、attempt read外json.loads100回。すでに開始時に全raw recordを保持する現実装では、この代表入力の重複負荷は元file再readよりrequest再生成・照合側にあります。これらはinclusive区間の実計数であり、個々の計算費用の排他的配賦や最適化の先取りではありません。

内部previous公開loadと独立loadはそれぞれ別にread／hash／parse各**200回**、unique100、最大2回、論理read **830,536 bytes**、request／比較各100回・match100。これをoperation探索のread重複に加算していません。store scopeのattempt read／hash／parseは0（chunk等の再読をattempt readとは数えない）。全12操作の翻訳snapshot本体の成功保存は**0件／0 bytes**で、既存digestのsnapshotを再確認してpointer更新するためです。新規seed生成時の履歴snapshot在庫はwrite総量の代わりにしません。unadoptedのrun順は一般には並列seedと異なり得るため、driverは新snapshot追加を許容し、全runの多重集合とそれ以外のsnapshot全field一致を確認します。

通常Python CPU raw秒はunadopted **2.333421／2.310880／2.331363**、adopted **.344007／.346182／.339618**、独立load **.186398／.191743／.183414**。outer process wall raw秒は同順に **2.608059／2.586300／2.610769**、**.620541／.619765／.610474**、**.463871／.464866／.456919**。親operation Pythonの生涯peak RSS rawはunadopted **67.234375／67.437500／67.250000 MiB**、adopted **69.765625／69.453125／69.531250 MiB**、独立load **66.531250／67.296875／66.765625 MiB**。全reuseのreaped child peakは0で、新worker submit／実requestも0。現translationのsaved_runs保持は100 records、adopted previous snapshotは1件で、全履歴snapshotはloadしていません。aggregate RSS・seed生成helper peak・物理I/O・cache residency等はunknownです。

証拠は追跡外の`work/issue57-worker-before/`：manifest／seed／seed-hashes、各caseのcommand／exit／metrics／diagnostic、observations／statistics／contribution／diagnostic-summary／cleanup。`work/issue57-worker-connection/`は生成した手順そのものによるsize2 Create成立の各12操作で、代表100の時間へ混ぜません。全24 fresh操作exit0、旧immutable／seed不変、completed・回答対応、追加request／worker submit0を照合し、所有一時workspace／人工credential／childのcleanup後、保持seedを含む全証拠fileをreadbackしました。Create／pstackは明示readして既存Skill／Mapだけへ翻訳探索を追加し、新Skillや汎用runnerは作っていません。

focused証拠は`work/issue57-worker-validation/`。selfcheck12件（翻訳のadopted／unadopted2件を追加）と既存translation70件、変更Python3fileのRuff check／format／tyが成功しました。初期Ruffのnested-ifとtyのmetrics型推論、初回全selfcheckの1失敗（unadoptedのrun順差を見落としたdigest同一の補助期待）を修正し、productionを変えず再実行しています。失敗は通常計測値へ混ぜていません。正式全check・Rootの別invocation直接確認・Reviewer gateはRoot担当で未受入れ、実provider／実workspace／意味品質／任意書籍／他OSは未確認です。

### 操作内catalogの実装後比較（Issue #57、Worker）

`translation.py`内だけで、単一translateの全元recordをhash／JSON検証後に保持し、実request textと先頭unit IDをそれぞれキーにして候補を絞り、取得後に全configを照合します。configはJSON byte列の索引キーにしません（等値な`-0.0`／`0.0`の再利用を狭めないため）。同じ候補長のrequestは再構築せず、候補内の辞書順・採用済みmanual優先・全実入力／規定repair／raw／schema／現在unit検証は残します。通常／repair／失敗／adjusted／分割失敗の全attemptはatomic保存成功後に登録し、登録と辞書順tuple snapshot取得を同じlockで保護します。取得済みsnapshotは固定、次の取得には登録済み値が見えます。v1の通常jobはdisjointなunit IDを実requestに含み、split子も交差しないため、同一request競合を作りません。後続v2用のsingle-flight基盤は追加していません。親のsnapshot／pointer、workerのimmutable保存、writer排他、公開loadの独立検証は不変です。

以下のWorker／Rootのafter値は、いずれも末尾のsigned-zeroレビュー修正**前**のsourceでの測定です。修正後の同seed after／代表CLI再計測は末尾のRoot最終確認に分離しています。過去値を修正後sourceの性能根拠として扱いません。

比較条件・script・固定interval・seed全byteはbeforeと同じです。成功した完全なafterは`work/issue57-worker-after-retry/`（通常各3回＋別diagnostic各1回）。初回`work/issue57-worker-after/`は8操作後のload-1 **seedコピーsetup中**にruntime timeoutで中断しました。残存workspaceは不完全コピーで、存在するseed fileは全hash一致、answers／child command／metricsは未生成でした。本番load開始の証拠はなく、OS／runtimeの停止原因は**unknown**です。中断側のraw・manifest・8操作と`interruption-inspection.json`を保持し、成功統計に混ぜていません。該当所有tempだけを確認して回収し、同一script／seed／予算で1回だけ新directoryへ再試行、12操作すべてexit0・cleanup／readback完了です。

| mode／区間 | after通常raw秒（3回） | median [min, max] 秒 |
| --- | --- | --- |
| unadopted **primary pre-job探索proxy** | .125422 / .119371 / .122516 | **.122516** [.119371, .125422] |
| unadopted 公開translate全体 | .199523 / .196265 / .194171 | .196265 [.194171, .199523] |
| adopted pre-job探索proxy | .085587 / .088819 / .087592 | .087592 [.085587, .088819] |
| adopted 公開translate全体 | .372309 / .381536 / .375316 | .375316 [.372309, .381536] |
| adopted 内部previous公開load（inclusive） | .202787 / .206893 / .203031 | .203031 [.202787, .206893] |
| 独立load操作全体（stdout serialize含む） | .211177 / .199479 / .203192 | .203192 [.199479, .211177] |
| 独立公開load wrapper（inclusive） | .209589 / .197940 / .201697 | .201697 [.197940, .209589] |

primary中央値はbefore 2.264825→after .122516秒、**beforeの5.4095%**で70%以下です。公開unadopted全体は2.332923→.196265秒。探索の全体寄与rawは62.8611%／60.8213%／63.0970%。これは人工attempt-heavy入力であり、実provider／実書籍の全体短縮率ではありません。adopted全体（.344570→.375316秒）や独立load（.186559→.203192秒）は速くなっておらず、独立load最適化を主張しません。代表PDF35／固定EPUB20 run／resumeの完了条件7はRootの別比較で判断します。

別diagnosticの元attempt read／hash／parseはadopted／unadopted探索とも各100回、unique100、同record最大1回、論理read415,268 bytes（beforeも100回で、この代表での改善は再readではなく候補探索側）。unadoptedのrequest／比較は各5,050→100回、match100／nonmatch0、attempt read外json.loadsは5,350→400回。adopted request／比較は各100回、attempt read外json.loadsは100→200回（索引構築分）。公開previous load／独立loadは各read／hash／parse200回、unique100、最大2回、830,536 bytesで不変。store／operation_other scopeの元attempt再読は0です。worker探索がないこの代表ではworker submit0であり、worker探索のread削減量をこの入力から断定しません。新規登録／split等の挙動は別の回帰で確認しました。全12操作のsnapshot本体新規保存0件／0 bytes、実追加request0、旧immutableとseed全byte一致・completed／回答対応をassertしました。

通常CPU raw秒はunadopted .198907／.195615／.193919、adopted .370489／.380233／.374029、独立load .210600／.198892／.202555。outer process wall raw秒は同順に .518304／.502026／.494354、.758264／.697614／.698295、.515093／.500681／.497476。親operation Python生涯peak RSS rawはunadopted 67.093750／67.250000／67.250000 MiB、adopted 72.796875／69.593750／70.093750 MiB、独立load 66.984375／67.015625／67.265625 MiB（macOS bytesから換算、import含む）。reaped child peakは全0で新helperなし。同時aggregateではありません。catalog保持は元raw100 records＋候補索引、previous採用snapshotは1件、全履歴snapshot／PNG一括保持なし。原record総file bytes415,268はPython heap使用量ではありません。物理I/O／cache residency／seed helper peak／aggregate RSS／実provider／OCR・意味品質はunknownです。

focusedは`work/issue57-worker-implementation-validation/`：既存translation70件を実装直後に確認し、catalog回帰追加後translation／resume／revise／helper selfcheck計153件が85.01秒で成功しました。追加回帰はatomic保存後の通常／repair成功・失敗／adjusted／分割失敗登録、別threadからの辞書順snapshot取得、orphanの辞書順選択・元read各1回／request4回、別操作の無関係破損拒否です。既存のmanual履歴・config/context変更拒否・raw/domain再検証も同suiteで維持しました。generate／save／domain validatorのfake・公開テストAPI・秒数pytestは追加していません。Rootの正式全check・独立公開確認・代表CLI比較・size2手順再確認・Reviewer gateは別担当で、このWorker比較だけで全受入れ完了とはしません。

その後Rootが別invocationで同seedのafter12操作、代表CLI after12操作、直接API5ケース、新規size2 Create12操作を独立確認し、いずれもexit0と報告しました。Rootのprimary中央値は2.282169→.133174秒（beforeの5.8354%）、PDF35はrun 3.313561→3.417744秒／resume .823617→.799454秒、固定EPUB20はrun 4.105198→4.129478秒／resume .388491→.384191秒で、4操作とも`max(before×1.10, before＋.05秒)`以内です。Worker値とは別系列で、raw／min/max／上限は`work/issue57-root-checks/performance-comparison.json`、操作証拠は`work/issue57-root-after/`・`work/issue57-root-cli-after/`・`work/issue57-root-direct/`・`work/issue57-root-create-after/`にあります。この時点で正式全checkとReviewはこれからであり、性能・直接確認の成功だけを最終受入れとはしません。

#### レビュー修正：等値configのsigned zero

fresh Correctness reviewは指摘なし、Ponytail reviewの1件をRootが採用しました。初版catalogのconfig JSON byteキーは、正規の`GenerationConfig`として等値な`temperature=-0.0`／`0.0`を分離していました。Rootの実Node／fauxによる公開translate→pointer除去→反対符号のconfigでtranslate→loadでは、旧HEADは追加request0、初版catalogは1で、両方completedでした（`work/issue57-root-checks/check_signed_zero.py`、`work/issue57-root-signed-zero/observations.json`）。

修正は候補indexのconfig dimensionとprivate取得APIの不要config引数を除くだけです。text／先頭unit IDの候補取得後にある既存config比較／`_saved_answer`の全照合、実request／raw／domain検証、辞書順・manual優先・全破損拒否・atomic保存とthread可視性は変えません。公開schema／serializer／数値正規化・Plan変更はありません。公開API回帰は正負両方向で、元record設定を書き換えずに成功attemptを回収し、追加送信ゼロ・旧immutable byte保持・現在config対応・新旧loadを確認します。異なる実configの無効化は既存`test_translation_resends_when_actual_input_changes`を維持します。Workerの修正後確認はfocused12件（5.72秒）、translation module77件（51.58秒）、既存helper selfcheck12件（2.23秒）がすべてskipなし・exit0、Ruff check／format（75 files）・ty・diff-checkもexit0です。秒数はテスト実行記録で性能判定値ではありません。証拠は`work/issue57-worker-correction/`、正式全check・直接API・修正後性能の再確認と最終ReviewはRoot担当です。

#### レビュー修正後のRoot最終確認

同seed・同計測script・同境界・通常各3回（diagnosticは別）の最終sourceでは、primary探索中央値 **2.282169→.122124秒（beforeの5.3512%）**、公開translate全体 **2.352569→.197338秒**でした。探索read／hash／parseは元100record各1回、request／比較は5,050→100回、公開loadの各200回は別scopeのままです。primary親生涯peak RSS中央値は67.281250→67.109375 MiBで、差をcatalog単独の寄与とは断定しません。保持は100 raw records＋索引に限り、全履歴snapshot／画像・永続cacheは追加していません。

代表PDF35はrun **3.313561→3.457074秒**／resume **.823617→.858901秒**、同byte固定EPUB20はrun **4.105198→4.286605秒**／resume **.388491→.407141秒**で、全4操作が`max(before×1.10, before＋.05秒)`以内です。修正前の有利な値へ置換していません。raw／median／min/max／上限と全command／exitは`work/issue57-root-correction-checks/performance-comparison.json`、`work/issue57-root-final-after/`、`work/issue57-root-cli-final/`を参照します。全reuseで新request0・旧immutable／seed不変、所有物cleanupと証拠readbackを確認しました。

Rootの修正後正式チェックはcore-only **929 passed／123 skipped**（Docling不在）、PDF正式4ファイル **181 passed**、Ruff check／format・ty・公開import・CLI help・diff-checkすべてexit0／警告なしです。各commandとlogは`work/issue57-root-correction-checks/repository-checks.json`。公開APIのmanual三世代優先・履歴／無関係破損拒否・repair保存失敗からの無送信回収・split部分成功保持／orphan回収は`work/issue57-root-direct-final/`、signed zeroは旧HEAD／最終sourceとも追加request0を`work/issue57-root-signed-zero-final/`で確認しました。最終ReviewerとCode Review gateはこの記録とは別の承認工程です。実provider／実workspace／OCR・意味品質・任意書籍・他OS・remote CIは未確認のままです。

## 準備collectの制限付き並列（Issue #60）

### 測定・再実行・限界

公開`prepare_document(concurrency=1|2|4)`のcollectだけを並列化し、既定1・group／実wire／config／根拠検査を維持します。run／resumeのCLIは`--preparation-concurrency {1,2,4}`、run manifest保存・resume未指定保存値・旧field欠落1です。設定変更だけでは再送しません。[artifact契約](artifacts.md#文書contextglossary準備issue-11)と[利用上の注意](usage.md#文書を翻訳する通常利用)を参照してください。PDF補正並列は別Work Itemです。

再実行はrepository root、既存Python／Node／compiled helperと同梱resource全10file hash一致、人工入力・credential・実Node公開faux／no-networkだけを前提とします。不足時は止め、install／sync／build／provider通信をしません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# 未作成のignore済みwork直下。ほかのbenchmark/testと非並行・foreground。
.venv/bin/python .pi/skills/verify-project/scripts/preparation_concurrency.py \
  --evidence work/issue60-concurrency-new
```

専用helperは既存testの自作PDF型source／実EPUB archive reader・extractorと公開save→prepare→loadを使います。EPUBの同一archive byteと、各12個の独立heading＋body groupを固定し、collectだけ各500ms待ち、organizeは遅延なしです。設定1／2／4ごとにfresh Python通常3回、warm filesystem・flushなしで実行します。timerは**公開prepare全体**で、Node helper起動・実validator・attempt／根拠／snapshot／pointer保存を含みます。source生成／saveと別公開loadは区間外で、外側Python process全体wallも各commandへ別記します。秒数閾値をpytestへ追加しません。

期待はstage median(c2/c1)<=.75、c4/c1<=.55。実faux callbackのstart/endをPID付き共通monotonic nsで記録して跨processでactiveを集約します。process-local counter・pre-send許可・helper生存数を実activeと呼びません。上限以下・最終0、calls13（collect12＋organize1）、総送信text bytes・全実request／config・raw回答・collections／runsのgroup対応が同じで、organizeは全collect保存後に一度だけです。別の無変更prepareでは追加送信0・旧immutable不変を検査します。

証拠はmanifest（版／dirty／diff・source／input／helper hash・条件／unknown）、保存したverification-sources、各caseのcommand／exit／stdout／stderr、timeline／requests／comparable／immutable-hashes、results／summary／cleanupです。失敗は途中証拠を残して停止し、成功統計へ混ぜません。所有child groupだけ60秒でSIGINT→45秒drain→必要時SIGKILL／wait、各所有TemporaryDirectory／人工credentialをfinallyで削除し、childの強制終了時も親がreap後に今回の新規sample内の所有tempだけを回収します。証拠は残して全参照をreadbackします。既存workspace／lock／venv／model／他PIDは操作しません。

[既存Skill](../../.pi/skills/verify-project/SKILL.md#準備collectの制限付き並列issue-60)／[Mapの対応節](../../.pi/skills/verify-project/references/feature-map.md#準備collectの制限付き並列issue-60)に逆順、同時missのraw成功／失敗共有・binding／usage一度、局所失敗／Event取消／OS replace保存例外→旧pointer保持・成功回収の直接操作とfocused回帰をまとめています。これらの手順成立だけでRootの別invocation直接受入れ、正式全phaseやReviewer gateを代用しません。

この比較は制御遅延で待ちを重ねられることの確認であり、実providerの速度・rate limit・意味品質／料金は未確認です。失敗時は他jobも開始済みのため、逐次より停止前の利用量が増え得ます。共有はoperation内rawだけで、永続cache／全履歴snapshot常駐は追加しません。物理I/O・同時aggregate RSSはunknown、実課金のexactly-once保証もありません。既定は1を維持します。

### Workerの通常比較（signal取消レビュー修正前）

以下は公開prepare単体のSIGINT／SIGTERM取消レビュー修正**前**の値です。修正後sourceの成功・性能根拠には流用せず、Rootが同helperの18sampleと必要な正式／直接確認を別証拠へ再実行します。HEAD `30baa7b`＋当時のIssue #60未commit差分、Python 3.14.9／Node v24.22.1／macOS arm64。測定時sourceとhelperの全hashは証拠manifestに固定しています。PDF型・実EPUBの全18sampleで同一calls13、総送信message text **20,289 UTF-8 bytes**（画像0）、実request／config・raw回答・group順が一致しました。各設定の実callback peakは毎回1／2／4、最終active0、無変更prepareの新規送信0・旧immutable不変です。

| 入力 | 上限 | 通常raw秒（3回） | median [min, max] 秒 | c1比 |
| --- | ---: | --- | --- | ---: |
| PDF型 | 1 | 8.164316 / 8.000252 / 8.034608 | 8.034608 [8.000252, 8.164316] | 1 |
| PDF型 | 2 | 4.117016 / 4.068510 / 4.059467 | 4.068510 [4.059467, 4.117016] | .506373 |
| PDF型 | 4 | 2.214852 / 2.238591 / 2.213771 | 2.214852 [2.213771, 2.238591] | .275664 |
| 実EPUB | 1 | 7.967004 / 7.929837 / 7.986748 | 7.967004 [7.929837, 7.986748] | 1 |
| 実EPUB | 2 | 4.049606 / 4.091509 / 4.045799 | 4.049606 [4.045799, 4.091509] | .508297 |
| 実EPUB | 4 | 2.206551 / 2.205924 / 2.210751 | 2.206551 [2.205924, 2.210751] | .276961 |

証拠は追跡外`work/issue60-worker-accepted-20261110-105305/`。親の所有temp回収補完前の同production比較`work/issue60-worker-final-20261110-104207/`も全18sample成功ですが、上表へ混ぜず保持しています。初回探索の`work/issue60-worker-20261110-103546/`はorganizeにも500ms待ちを入れた別条件で、上表に混ぜていません。Map手順自身によるlocal／cancel／saveの直接再実行は`work/issue60-worker-recovery-final-xy8MH4/`で、3ケースとも旧pointer保持・保存成功1件回収・不足collect4＋organize1だけ追加送信・旧immutable不変、全開始callback終了とcleanup/readbackを照合しました。上記の性能・復旧操作・focused moduleのpreparation／run／resume／render／translation／helper selfcheck計376件成功（skipなし）は、いずれもsignal取消レビュー修正前sourceの記録です。修正後の保証確認や性能値には置換しません。正式全check・Root独立操作・Reviewer gateは別担当で、未承認の実provider評価や既定変更の根拠にはしません。
