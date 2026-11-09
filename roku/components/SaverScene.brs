sub init()
    m.a = m.top.findNode("a")
    m.b = m.top.findNode("b")
    m.msg = m.top.findNode("msg")
    m.tick = m.top.findNode("tick")
    m.front = m.a
    m.back = m.b
    m.fails = 0
    m.n = 0
    m.top.observeField("imageUrl", "onUrl")
    m.top.observeField("interval", "onInterval")
    m.a.observeField("loadStatus", "onLoadedA")
    m.b.observeField("loadStatus", "onLoadedB")
    m.tick.observeField("fire", "onTick")
    m.top.setFocus(true)
end sub

sub onUrl()
    m.tick.control = "start"
    onTick()
end sub

sub onInterval()
    if m.top.interval >= 5 then m.tick.duration = m.top.interval
end sub

sub onTick()
    if m.top.imageUrl = "" then return
    m.n = m.n + 1
    ' cache-buster so the Roku fetches a fresh frame every time
    sep = "?"
    if Instr(1, m.top.imageUrl, "?") > 0 then sep = "&"
    m.back.uri = m.top.imageUrl + sep + "n=" + m.n.toStr()
end sub

sub onLoadedA()
    loaded(m.a)
end sub

sub onLoadedB()
    loaded(m.b)
end sub

sub loaded(p as Object)
    if not p.isSameNode(m.back) then return      ' only the poster that is loading the next frame matters
    st = p.loadStatus
    if st = "ready"
        m.fails = 0
        m.msg.text = ""
        m.back.visible = true
        m.front.visible = false
        t = m.front
        m.front = m.back
        m.back = t
    else if st = "failed"
        m.fails = m.fails + 1
        if m.fails >= 4 then m.msg.text = "dashboard unreachable - retrying" + Chr(10) + m.top.imageUrl
    end if
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    ' Any key ends the screensaver (the OS handles that); as a plain channel, back exits.
    return false
end function
