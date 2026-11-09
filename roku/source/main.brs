' House Dashboard - Roku screensaver (and a plain channel you can open from Home).
' The Pi renders the dashboard to a PNG every few seconds (pi/render.py); this just shows it.
' URL comes from source/config.brs, written by build.sh.

sub Main(args as Dynamic)
    showDashboard(false)
end sub

sub RunScreenSaver(args as Dynamic)
    showDashboard(true)
end sub

sub showDashboard(isSaver as Boolean)
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.setMessagePort(port)
    scene = screen.CreateScene("SaverScene")
    screen.show()
    scene.imageUrl = dashImageUrl()
    scene.interval = dashInterval()
    scene.isSaver = isSaver
    while true
        msg = wait(0, port)
        if type(msg) = "roSGScreenEvent" and msg.isScreenClosed() then return
    end while
end sub
