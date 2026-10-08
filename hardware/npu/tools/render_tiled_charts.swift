import Foundation
import AppKit

let args=CommandLine.arguments
if args.count != 3 { fatalError("usage: render_tiled_charts.swift performance_summary.csv output_dir") }
let lines=try String(contentsOfFile:args[1],encoding:.utf8).components(separatedBy:.newlines).map { $0.trimmingCharacters(in:.whitespacesAndNewlines) }.filter { !$0.isEmpty }
let keys=lines[0].split(separator:",").map(String.init)
let records=lines.dropFirst().map { line -> [String:String] in
    Dictionary(uniqueKeysWithValues:zip(keys,line.split(separator:",").map(String.init)))
}
func row(_ name:String)->[String:String] { records.first { $0["mode"]==name }! }
func val(_ r:[String:String],_ key:String)->Int { Int(r[key]!)! }
let output=URL(fileURLWithPath:args[2],isDirectory:true)
try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
let W=1600,H=1000
func color(_ hex:UInt32)->NSColor {
    NSColor(srgbRed:CGFloat((hex>>16)&255)/255,green:CGFloat((hex>>8)&255)/255,blue:CGFloat(hex&255)/255,alpha:1)
}
let ink=color(0x183149),muted=color(0x526476),navy=color(0x214B70),teal=color(0x007E80),light=color(0xE5EEF4),bg=color(0xF5F8FB)
var labels:[NSRect]=[]
func text(_ value:String,_ x:CGFloat,_ y:CGFloat,_ size:CGFloat=24,_ c:NSColor=ink,_ bold:Bool=false) {
    let attrs:[NSAttributedString.Key:Any]=[.font:bold ? NSFont.boldSystemFont(ofSize:size):NSFont.systemFont(ofSize:size),.foregroundColor:c]
    let s=NSAttributedString(string:value,attributes:attrs),m=s.size()
    let b=NSRect(x:x,y:y,width:m.width,height:m.height)
    precondition(b.maxX<=CGFloat(W) && b.maxY<=CGFloat(H) && b.minX>=0 && b.minY>=0,"Text exceeds canvas: \(value)")
    precondition(!labels.contains { $0.intersects(b) },"Labels overlap: \(value)")
    labels.append(b);s.draw(at:NSPoint(x:x,y:CGFloat(H)-y-m.height))
}
func rect(_ x:CGFloat,_ y:CGFloat,_ w:CGFloat,_ h:CGFloat,_ c:NSColor) {
    c.setFill();NSBezierPath(rect:NSRect(x:x,y:CGFloat(H)-y-h,width:w,height:h)).fill()
}
func number(_ n:Int)->String { let f=NumberFormatter();f.numberStyle = .decimal;return f.string(from:NSNumber(value:n))! }
func render(_ name:String,_ draw:()->Void)throws {
    labels=[]
    let image=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:W,pixelsHigh:H,bitsPerSample:8,samplesPerPixel:4,
        hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState();NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:image)
    rect(0,0,CGFloat(W),CGFloat(H),bg);draw();NSGraphicsContext.restoreGraphicsState()
    try image.representation(using:.png,properties:[:])!.write(to:output.appendingPathComponent(name))
}
let base=row("baseline"),selected=row("tile16")
let b=val(base,"network_cycles"),o=val(selected,"network_cycles")
try render("performance.png") {
    text("TinyCNN-8 | same compute array, better schedule",64,40,36,ink,true)
    text("Measured standalone RTL cycles; synthetic data; unchanged 4 x 8 Matrix Unit",64,94,23,muted)
    rect(64,148,1472,145,.white)
    text("NETWORK LATENCY",92,168,19,muted,true)
    text("\(number(b))  to  \(number(o)) cycles",92,207,36,ink,true)
    text(String(format:"%.2f%% fewer cycles",100*(1-Double(o)/Double(b))),980,170,30,teal,true)
    text(String(format:"%.3fx speedup",Double(b)/Double(o)),980,219,28,teal,true)
    rect(750,327,18,18,navy);text("Whole network",780,321,22)
    rect(1100,327,18,18,teal);text("Conv2 only",1130,321,22)
    let names=[("baseline","Original schedule"),("overlap","Gather/load overlap"),("tile8","Spatial tile 8"),("tile16","Spatial tile 16"),("tile32","Spatial tile 32")]
    let x:CGFloat=350,maxw:CGFloat=930
    for (i,item) in names.enumerated() {
        let r=row(item.0),y=CGFloat(386+i*90)
        text(item.1,68,y+9,23,ink,item.0=="tile16")
        let total=val(r,"network_cycles"),conv=val(r,"conv2_cycles")
        rect(x,y,CGFloat(total)/CGFloat(b)*maxw,24,navy)
        rect(x,y+31,CGFloat(conv)/CGFloat(b)*maxw,24,teal)
        text(number(total),x+CGFloat(total)/CGFloat(b)*maxw+14,y-1,21,navy)
        text(number(conv),x+CGFloat(conv)/CGFloat(b)*maxw+14,y+30,21,teal)
    }
    text("Same 1,440 Conv2 Matrix transactions; tile16 weight rows: 5,760 to 360.",64,881,24,teal,true)
    text("Model loading excluded. Not CPU/SoC speedup, trained KWS accuracy, Fmax, area or power.",64,935,21,muted)
}
try render("tradeoff.png") {
    text("Spatial tiles | latency versus bounded storage",64,40,36,ink,true)
    text("16 is the selected default, not the fastest point or a proven PPA optimum.",64,96,24,muted)
    rect(64,158,1472,126,.white)
    text("TILE16 STORAGE BUDGET",92,178,19,muted,true)
    text("512 B psum  +  64 B tags  +  8 B packs  =  584 B",92,220,32,ink,true)
    let heads:[(String,CGFloat)]=[("Tile",80),("Network cycles",280),("Conv2 cycles",560),("Weight rows",820),("Major storage",1100)]
    for h in heads { text(h.0,h.1,332,24,muted,true) }
    for (i,name) in ["tile7","tile8","tile16","tile32"].enumerated() {
        let r=row(name),y=CGFloat(404+i*78)
        if name=="tile16" { rect(64,y-12,1472,62,light) }
        text(String(val(r,"tile")),80,y,28,ink,name=="tile16")
        text(number(val(r,"network_cycles")),280,y,28,ink,name=="tile16")
        text(number(val(r,"conv2_cycles")),560,y,28,ink,name=="tile16")
        text(number(val(r,"conv2_weight_rows")),820,y,28,ink,name=="tile16")
        text("\(number(val(r,"major_tile_storage_bytes"))) B",1100,y,28,ink,name=="tile16")
    }
    let r32=row("tile32"),extra=val(selected,"network_cycles")-val(r32,"network_cycles")
    rect(64,750,1472,130,.white)
    text("Tile16 to tile32: +512 B psum",92,772,28,ink,true)
    text("Saves \(number(extra)) more network cycles; control and profiler registers are additional.",92,817,23,muted)
    text("Declared logical capacity is not synthesized area. No SRAM macro, timing or power claim.",64,928,21,muted)
}
print("Rendered performance.png and tradeoff.png")
