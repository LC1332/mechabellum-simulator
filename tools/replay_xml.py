# Extract the embedded BattleRecord XML from a .grbr replay file
# usage: python replay_xml.py file.grbr out.xml
import sys

def extract(path, out):
    data = open(path, "rb").read()
    i = data.find(b"<BattleRecord")
    if i < 0:
        raise SystemExit("no BattleRecord XML found")
    j = data.find(b"</BattleRecord>", i)
    if j < 0:
        raise SystemExit("no closing tag")
    xml = data[i : j + len(b"</BattleRecord>")]
    open(out, "wb").write(xml)
    print(f"extracted {len(xml)} bytes of XML -> {out}")
    return xml

if __name__ == "__main__":
    extract(sys.argv[1], sys.argv[2])
