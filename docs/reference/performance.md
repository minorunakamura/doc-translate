# PDF／EPUBのoffline性能baselineとrenderer索引（Issue #54／#55）

[Approved Plan](../plans/pdf-epub-performance.md)のA／B／B2・第1段階のbaselineと、第2段階の優先1（renderer関連付け索引）だけを扱います。Issue #55は現在入力のblock／unit／cell／node／target索引で探索を置き換え、schema・request方式・並列数・statusの共有検証経路は変更しません。同revisionのbefore／afterラベルは**再現性のcontrol比較**であり、高速化の証明ではありません。変更前後は別invocationの最初のbeforeラベル3回をprimaryとして固定します。Planにある旧revisionの秒数（特にB2のwarm Python process）は現在の測定に流用しません。attempt索引・ID分離・新並列、実provider／OCR／layout／model取得・意味品質・料金・任意規模の保証は対象外です。

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
