# レビュー所見2〜6：正しい出力判定・読順・安全な継続・保存容量・準備上限

## 目的
通常利用で、正しく出た訳文を失敗扱いせず、EPUBの手順を原順で訳し、続けられる準備は自動で続け、続けてはいけない翻訳障害では余分に送らない。翻訳履歴の容量問題は同条件の実測から判断する。対象は minorunakamura/doc-translate の main（調査時591086c）。一つのPlan、所見別の成果。PDF初回原文の #97〜#101・その正式Planは変更せず、完了を待たない。

## 要件・決定事項
- 見かけ上のcompletedや通過率より、出力内容・読順・本物の参照異常検出・成功済み成果の再利用を優先する。保存済み訳や手動訳を勝手に書き換えない。
- 準備の入力上限は全モデル一律の文字数ではない。明示された input_limit で、内容を保ったまま小さくできる場合は自動で分けて準備から本文翻訳へ進む。collectとorganizeの**両方**を扱う。「同じresumeを案内」「警告だけ」「用語集を空にして通す」は解決ではない。追加呼出しは失敗範囲に限り有限とし、正常に入る既存要求は分けない。
- 逆に翻訳で auth_failed / storage_failed を認識したら、残りのジョブや新たな送信を開始しない。すでに進行中の成果・失敗を捏造せず回収する。一件だけの訳の不合格や、縮小で回復可能な上限失敗とは区別する。
- 所見5は計測と判断まで。過去の履歴削除・全利用者の保存方式変更は今回承認する実装ではない。

## 確認できたこと
1. **参照誤警報**：実書籍 work/design-and-build-great-web-apis/ の render.json が指す renders/79aa5af…/summary.json は2,358単位到達、render_unit_coverage成功・render_references失敗。日英Markdownの7188行と12533行はともにコードフェンス内。mainのrender._buildは全文のid/href/src部分文字列を検査する。閉じた#65の別ブランチに原因を直す b24e679 がある。mainに修正済みではない。
2. **EPUB読順**：人工EPUBの親liに Before、子リストChild、その後にAfterを入れると、sourceは正順だがunitsはBeforeAfterとChildの2件。英語も人工和訳も親の全文→子になった。tests/test_units.py の既存期待にこの結合が明記される。訳文には親を二つに戻せる位置がない。
3. **翻訳停止**：3件の人工ジョブで2件目に推論境界の失敗を注入。並列1ではauth_failedを採用した後も3件目が成功した。並列2のstorage_failedでも、失敗で空いた枠に未開始の3件目が入った。確認したのは投入・採用であり実課金ではない。
4. **容量**：実書籍の snapshots/translation/*.json は802ファイル、3,261,065,799 bytes（複数実行分の論理サイズ）。3ジョブの人工成功初回は3件9,984 bytes、成功状態を同条件で再実行すると増分0件0 bytes。失敗ジョブが再試行された例は1版増加した。_adoptはジョブ終了時に全chunk・runs等を含むsnapshotとpointerを保存する。同じdigestの増分ゼロとpointer書込みゼロは別。
5. **準備上限**：隔離したfauxで人工capを1 byteにするとcollectは同一要求で2回ともinput_limit・採用なし。別Probeではcollect成功後にorganizeだけを上限拒否させると、再開でcollectは再利用したが、同じorganizeを再投入して再度失敗（同一request hash）。prepare._groupsは見出し・resourceで区切り、organizeは全回答を1要求に載せる。現load_preparationは元group数＋organize1件の対応も再検証する。実書籍では407 collect＋1 organizeは成功。実書籍の再現失敗や特定モデルの固定上限を主張しない。

## 制約・対象外
- 旧workspace・実書籍・失敗/marker・旧snapshotは読取専用。実認証・実provider送信・課金試験・追加install・旧実験ブランチのmergeなし。証拠は人工あるいは非秘密metadata。無関係な#65の件数表示変更、PDF補正、旧訳を自動分割する移行、汎用scheduler・DB・新UIは対象外。
- 既存の認証・before_send・取消・既存保存のhash/atomic検証・手動優先・marker保護・対応する旧版の再読を維持。失敗時に生回答やログを修復しない。未知のproviderエラーをサイズ超過と決めつけない。

## 実装方針
**2｜出力QA**：render.pyの生成箇所で、日英別に実際に出したアンカー・内部リンク先・画像を記録し照合する。b24e679の該当部分と回帰だけを現在の全出力経路に照らして採用する。コード中の文字でanchor到達・source-map・追加anchor補完が成立しないようにする。status --verifyが古い誤判定を再計算結果にかぶせないこと。軽量statusは保存結果の表示のまま。実際の重複・リンク切れ・未許可asset・別要因のpartialは残す。

**3｜EPUB**：units.pyとrender.pyを対で直す。新規EPUBのliでは子リストの前後を原本のcontent順で別々に翻訳できるようにし、子の後の文は親項目の続きとして表示する。番号・開始番号・深さ・親アンカーは一度だけ。inline保護・属性・source-map上の位置・複数の子/空の前後・対象外unitを維持。source抽出結果そのものの書換えは不要。準備/翻訳の再読はunitを再生成するため、適用方式を記録して旧方式の保存済み資産をその方式で厳密に検証・表示する。旧親unitの手動訳を文字数で切る、ずれたIDへ流す、無変更resumeで勝手に再訳することはしない。既存workspaceで訂正版が必要な場合は既存の新規実行を案内する。

**4｜翻訳の全体停止**：translation.pyのworkerで全体に及ぶ失敗を認識した時点で未開始jobと以後の送信（自動retry・分割の次の子も含む）を止める。schedulerがfutureを読む時だけに頼らず、送信境界の取消・競合を確認する。進行中を回収し、検証・保存済みの成功、停止原因、送信済みだが結果不明、未開始を混同しない。利用者取消へのすり替えや未開始の失敗計上をしない。実際のローカル保存例外では新規投入を止めて最後の整合pointerを維持し、保存不能なのに保存成功を約束しない。条件解消後の明示resumeで既存の成功を再利用する。

**5｜容量測定**：translation._adopt/_store と既存translation_baseline・run/resumeのfixtureを再利用。人工入力、revision、設定、並列度、chunk数を固定し、新規初回・無変更resume・失敗1件を回復するresumeを分ける。異なる文書サイズ（例：既存recipeの10/100 group）で新規ディレクトリから各3回、翻訳snapshotの追加件数・論理bytes・最大版サイズ、pointer更新、attempt/chunkを分離。時間は副次値。medianと範囲、実書籍の累積値と区別した再現コマンド・結果を docs/reference/performance.md に記す。全jobごとの全体保存が主要因か、同一digest再利用、強制終了後の回復に必要な情報を示す。完了条件は計測・判断であって削減率ではない。改善に値すれば保存互換・中断耐性と比較目標を伴う別提案にし、この項目だけで履歴を捨てない。

**6｜準備の自動継続**：上限に当たらない要求は現行どおり。確定input_limitの場合のみ、collectは元順序の連続unit群へ、ひとつの長いunitも元ID・正確な範囲と保護トークンを維持できる境界へ分ける。各部のquoteを元のsourceに検証・再対応付けし、重複・欠落・範囲外は拒否。organizeも大きければ候補・contextを有限の部分に分け、根拠を元collectionに保持したまま段階的に統合する。同じ用語の競合は暗黙の後勝ちにせず同一根拠に基づいて一貫した一つの採用結果を作り、利用者指定は最優先。global context・採用語・根拠・分割結果と元入力の対応を再読で検証する。全体整理で毎段同じ巨大payloadを再構築しない。必要な最小の方式識別・処理済み部分の証拠を既存attempt/evidenceに持たせ、厳密に一致する成功だけをresumeで再利用する。旧1/2形式のloadを緩めない。分割は入力が厳密に小さくなる有限個の境界だけで行い、追加呼出しにも有限上限を設ける。固定の指示だけで上限を超えるなど最小の安全な入力も入らない場合は、phase・何が入らないか・上限に余裕のある対応モデル等の対処を表示。内容切捨てや同一入力の無限再送は禁止。未知上限を推測した固定文字数・機種依存の定数を入れない。

文書は docs/reference/artifacts.md（新旧契約・停止/再利用）、docs/reference/usage.md（利用者の結果・再開案内）、CONTRIBUTING.md（変更影響別チェック）、上記性能reference、.pi/skills/verify-project/ の関係節を更新する。READMEは操作が変わる場合の導線のみ。

## 完了条件
- 日英でコードliteralによる参照誤警報が消え、本物の不正は検出する。旧summaryを評価したstatus --verifyと再renderの違いが説明できる。
- 新規EPUBで「前→子→後」が翻訳入力・英語・日本語・mapでそれぞれ一度ずつ正順。旧形式の準備/翻訳・手動訳の厳密な再読と無変更時の非再送を保つ。
- 認証/保存失敗後の未開始job投入・新送信0。先に開始したものと未開始を区別し、可能な成功を保持。回復後は欠けた分だけ実行。局所不合格の扱いを退行させない。
- 容量は同条件の3場面で比較でき、成功済み無変更再開のsnapshot論理増分0を確認。増分があれば原因を報告。既存資料を削除しない。
- 一括では入らず各部分なら入る人工文書で、collect超過、organize超過をそれぞれ再現し、利用者の手作業なしに固定用語集を採用し本文翻訳まで到達する。全本文範囲・用語根拠・ユーザー指定・再読・再開を確認。単に入力を空にしたり、不正な応答や途中失敗を成功にしない。分割不能は有限停止し、認証・保存失敗を分割再試行しない。

## 動作確認方法
- 軽量な失敗経路のある対象だけ既存tdd Skillを使う。2：公開render_documentの偽/真の参照、3：generate_translation_unitsとrenderの順、4：translate_documentで人工障害後の投入/保存、6：prepare_document→load_preparationで確定上限後の段階処理・厳密な対応をtest-firstにする。旧形式再読、再開、CLI案内は必要な回帰/直接操作。5のbaselineや文書・計測整理は非TDD。
- 通常のCONTRIBUTING.mdに従い tests/test_render.py、test_units.py、test_preparation.py、test_translation.py、test_resume.py、test_run.py、test_revise.py等から実差分の全caller・fixture consumerと旧byte fixtureを選ぶ。Python全体Ruff/format・既存環境ty、共有保存/fixtureならcore全pytest・関係PDF正式phaseへ。Nodeに変更が及ぶ場合のみNode側正式チェックも対象。無断build/installなし。
- verify-project/Feature Mapの「保存記録と詳細検査」「EPUB run／resume」「形式別run／resume性能」「preparation/translation保存attempt探索」「request内ID」「準備collectの制限付き並列」を関係部分だけ使う。#65の旧branch手順は参考だが現checkoutの成功証拠とはしない。今回不足する順序・障害注入・保存bytes・phase別capの起動→観測→対照→後片付けは共通create-verification-skillで既存資産に最小補完。外部providerでの情況や実課金をfaux成功から推定しない。
- Workerはfocused test・手順再実行。Rootは別の新しい隔離workspaceで現行製品入口を直接操作し、版・command/exit・日英出力順・QA・投入数/境界・採用/再利用hash・旧不変・容量を照合し、失敗も記録する。公開用に書籍本文・資格情報を含めない。所有temp・人工credential・childだけをcleanup/waitして保持証拠を再読。追加install/共有設定変更なしで安全に補完できる最小helperはRoot裁量。権限等で成立不能なら理由・再開条件付きBLOCKED、契約・保証変更ならREPLAN_REQUIRED。Plan承認とticket分割・公開は別。Implementationは別セッション。