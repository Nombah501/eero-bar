.pragma library

// Only explicitly named native controls participate. Keys never contain row indices.
function targets(item) {
    var found = []
    function visit(node) {
        if (!node || !node.visible || !node.enabled) return
        var name = String(node.objectName || "")
        if (name.indexOf("nav:") === 0 && node.interactive !== false && node.busy !== true)
            found.push({key: name.slice(4), item: node})
        var children = node.children || []
        for (var i = 0; i < children.length; i++) visit(children[i])
    }
    visit(item)
    // Ambiguous identities are inspectable by mouse, never keyboard-retargeted.
    return found.filter(function(target) {
        return found.filter(function(other) { return other.key === target.key }).length === 1
    })
}
function current(targets, key) {
    for (var i = 0; i < targets.length; i++) if (targets[i].key === key) return targets[i]
    return null
}
function move(targets, key, direction) {
    var index = -1
    for (var i = 0; i < targets.length; i++) if (targets[i].key === key) index = i
    if (!targets.length) return ""
    return targets[(index < 0 ? (direction < 0 ? targets.length - 1 : 0)
        : (index + (direction < 0 ? -1 : 1) + targets.length) % targets.length)].key
}
function activate(target) {
    if (!target) return false
    var item = target.item
    if (!item.visible || !item.enabled || item.interactive === false || item.busy === true) return false
    if (typeof item.clicked === "function") item.clicked()
    else if (typeof item.toggled === "function") item.toggled()
    else item.forceActiveFocus()
    return true
}
if (typeof module !== "undefined") module.exports = {targets: targets, current: current, move: move, activate: activate}
