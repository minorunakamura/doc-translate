# Issue tracker

## Tracker
GitHub Issues

## Repository
minorunakamura/doc-translate
https://github.com/minorunakamura/doc-translate

## 作成・取得
- 公開gh CLIを使い、常に --repo minorunakamura/doc-translate を指定する。
- 作成は gh issue create、取得は gh issue view を使う。
- ready-for-agent対応名は既存triage設定があれば尊重し、
  なければ ready-for-agent を使用する。
- 不足するラベルは必要な1つだけ、別途承認後に準備する。

## 依存関係
- blockerを先に作り、本文にも実Issueリンクを残す。
- 利用可能なnative blocking relationshipを使用する。
- 認証・権限・通信エラーでは停止し、成功扱いしない。
- 元Issueの本文・状態を変更しない。
