pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import qs.Ui
import "EeroModel.js" as EeroModel

// Login flow for the popup: signedOut (Identifier -> "Send code") and
// codeSent (Verification code -> "Verify"). Mirrors
// /usr/share/omarchy/shell/plugins/panels/network/Panel.qml's inline
// passphrase prompt: onAccepted submits, Keys.onEscapePressed closes the
// whole popup (there is no separate "inline prompt" here to fall back to --
// the login view IS the popup body while signed out), and focus is claimed
// via Qt.callLater so it runs after layout settles.
//
// Fields are read directly (identifierField.text / codeField.text) rather
// than mirrored into a backing property: TextField.text is a genuine
// editable property, so a `text: someProperty` binding is silently dropped
// the first time the user types (any imperative write -- including the
// control's own internal handling of a keystroke -- clears a declarative
// binding). Clearing after submit is therefore done by assigning
// `field.text = ""` directly, never by resetting a property the binding no
// longer tracks.
Column {
    id: root

    required property var service // Service.qml, or null while unavailable
    // "signedOut" or a live one ("ready"/"error") during a re-login: the
    // identifier step shows whenever we are NOT in the code step; "codeSent"
    // once sendCode() has answered.
    required property string phase // signedOut | ready | error | codeSent | amazon
    required property color foreground
    required property string fontFamily

    signal closeRequested()
    signal navigationRequested(string key)

    readonly property bool loginBusy: root.service ? root.service.loginBusy === true : false
    readonly property string maskedIdentifier: root.service ? String(root.service.maskedIdentifier || "") : ""
    readonly property var lastError: root.service ? root.service.lastError : null
    readonly property bool fieldFocused: identifierField.activeFocus || codeField.activeFocus

    function focusPrimaryField() {
        if (root.phase === "amazon") root.navigationRequested("login:cancel-amazon")
        else if (root.phase === "codeSent") codeField.forceActiveFocus()
        else identifierField.forceActiveFocus()
    }

    function submitIdentifier() {
        if (!root.service || root.loginBusy || identifierField.text.trim() === "") return
        var value = identifierField.text
        identifierField.text = ""
        root.service.sendCode(value)
    }

    function submitCode() {
        if (!root.service || root.loginBusy || codeField.text.trim() === "") return
        var value = codeField.text
        codeField.text = ""
        root.service.verifyCode(value)
    }

    function useDifferentAccount() {
        codeField.text = ""
        if (root.service) root.service.cancelLogin()
    }

    onPhaseChanged: codeField.text = ""
    onVisibleChanged: {
        if (!visible) {
            identifierField.text = ""
            codeField.text = ""
        }
    }

    spacing: Style.space(10)

    Text {
        width: parent.width
        textFormat: Text.PlainText
        text: "Sign in to eero"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
    }
    Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.phase !== "codeSent"
        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: root.phase === "amazon"
                ? (root.service.amazonPhase === "verification_ready" ? "New sign-in verified. Save this Session and replace the previous one?"
                    : root.service.amazonPhase === "exchanging" ? "Checking the new sign-in. Previous Session is unchanged."
                    : root.service.amazonPhase === "saving" ? "Saving the approved Session…"
                    : "Finish Amazon in your default browser, then paste the entire returned address into the local return page. Cancel here or close this popup to stop.")
                : "Amazon account: in your default browser, finish Amazon then paste its entire returned address into the local page. Default US only; Session save requires separate consent."
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
        }
        Button {
            objectName: root.phase === "amazon" ? "nav:login:save-amazon" : "nav:login:amazon"
            focusable: true
            text: root.phase === "amazon" ? "Save new Session" : "Sign in with Amazon"
            visible: root.phase !== "amazon" || root.service.amazonPhase === "verification_ready"
            enabled: !!root.service && (root.phase === "amazon" || !root.loginBusy)
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: {
                if (root.phase === "amazon") root.service.approveAmazonSession()
                else root.service.startAmazonLogin()
            }
        }
        Button {
            objectName: "nav:login:cancel-amazon"
            focusable: true
            text: "Cancel Amazon sign-in"
            visible: root.phase === "amazon"
            enabled: root.service && root.service.amazonPhase !== "saving"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.service.cancelLogin()
        }
    }


    // -- identifier (signedOut, or a re-login started from a live phase) --
    Column {
        width: parent.width
        visible: root.phase !== "codeSent" && root.phase !== "amazon"
        spacing: Style.space(8)

        TextField {
            id: identifierField
            objectName: "nav:login:identifier"
            width: parent.width
            placeholderText: "Email or phone (+E.164)"
            foreground: root.foreground
            // readOnly, not enabled: false, while a request is in flight --
            // disabling drops activeFocus with nothing to give it back to,
            // which left Esc/Tab dead until the popup was reopened. readOnly
            // blocks edits but keeps focus (and Keys.onEscapePressed below)
            // alive.
            readOnly: root.loginBusy

            onAccepted: root.submitIdentifier()
            Keys.onEscapePressed: root.closeRequested()
            Keys.onDownPressed: root.navigationRequested("login:identifier")
            onVisibleChanged: if (visible) Qt.callLater(identifierField.forceActiveFocus)
            Component.onCompleted: if (visible) Qt.callLater(identifierField.forceActiveFocus)
        }

        Button {
            objectName: "nav:login:send-code"
            focusable: true
            text: "Send code"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            opacity: enabled ? 1.0 : 0.5
            enabled: !root.loginBusy && identifierField.text.trim() !== ""
            onClicked: root.submitIdentifier()
        }

        Button {
            objectName: "nav:login:cancel"
            focusable: true
            text: "Cancel login"
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: {
                if (root.service) root.service.cancelLogin()
                root.closeRequested()
            }
        }
    }

    // -- codeSent: verification code ----------------------------------------
    Column {
        width: parent.width
        visible: root.phase === "codeSent"
        spacing: Style.space(8)

        Text {
            width: parent.width
            textFormat: Text.PlainText
            text: "Code sent to " + root.maskedIdentifier
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
        }

        TextField {
            id: codeField
            objectName: "nav:login:code"
            width: parent.width
            placeholderText: "Verification code"
            foreground: root.foreground
            readOnly: root.loginBusy // see identifierField's readOnly comment above
            inputMethodHints: Qt.ImhDigitsOnly

            onAccepted: root.submitCode()
            Keys.onEscapePressed: root.closeRequested()
            Keys.onDownPressed: root.navigationRequested("login:code")
            onVisibleChanged: if (visible) Qt.callLater(codeField.forceActiveFocus)
            Component.onCompleted: if (visible) Qt.callLater(codeField.forceActiveFocus)
        }

        Row {
            spacing: Style.space(8)

            Button {
                objectName: "nav:login:verify"
                focusable: true
                text: "Verify"
                bordered: true
                foreground: root.foreground
                fontFamily: root.fontFamily
                opacity: enabled ? 1.0 : 0.5
                enabled: !root.loginBusy && codeField.text.trim() !== ""
                onClicked: root.submitCode()
            }

            Button {
                objectName: "nav:login:different-account"
                focusable: true
                text: "Use a different account"
                foreground: root.foreground
                fontFamily: root.fontFamily
                opacity: enabled ? 1.0 : 0.5
                enabled: !root.loginBusy
                onClicked: root.useDifferentAccount()
            }
        }
    }

    Text {
        width: parent.width
        visible: !!root.lastError
        textFormat: Text.PlainText
        text: root.lastError ? EeroModel.errorText(root.lastError.code, root.lastError.message) : ""
        color: Color.urgent
        wrapMode: Text.WordWrap
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
    }

    Text {
        width: parent.width
        textFormat: Text.PlainText
        text: "OTP is a separate method for an existing eero email/phone login; an Amazon email does not make OTP supported. Amazon password/MFA stay on Amazon. Only the final eero Session is saved after your consent; no lifetime, rotation or other-region support is promised."
        color: Color.muted
        wrapMode: Text.WordWrap
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
    }
}
