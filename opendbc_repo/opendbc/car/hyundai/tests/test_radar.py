import pytest

from opendbc.can import CANParser
from opendbc.car import Bus, structs
import opendbc.car.hyundai.hyundaicanfd as hyundaicanfd
import opendbc.car.hyundai.radar_interface as radar_interface_module
from opendbc.car.hyundai.radar_interface import RADAR_MSG_COUNT3, RADAR_START_ADDR_CANFD3, RadarInterface
from opendbc.car.hyundai.values import HyundaiExtFlags, HyundaiFlags


class TestRadarGroup3:
  @staticmethod
  def parse(addr, dat):
    name = f"RADAR_TRACK_{addr:x}"
    parser = CANParser("hyundai_canfd_radar_generated", [(name, 20)], 1)
    parser.update([0, [(addr, bytes.fromhex(dat), 1)]])
    return parser.vl[name]

  def test_group3_active_track(self):
    track = self.parse(0x406, "e1043b0f02590e692a227e16f80fe00f28fcc753a20a0000")

    assert track["OBJECT_LENGTH"] == pytest.approx(4.4)
    assert track["LONG_DIST"] == pytest.approx(55.4)
    assert track["LAT_DIST"] == pytest.approx(-3.0)
    assert track["REL_SPEED"] == pytest.approx(4.4)

  def test_group3_empty_track(self):
    track = self.parse(0x407, "c03d3b0000000000ff0700000000000000d0020000000000")

    assert track["OBJECT_LENGTH"] == 0
    assert track["LONG_DIST"] == pytest.approx(204.7)
    assert track["LAT_DIST"] == 0
    assert track["REL_SPEED"] == 0

  def test_group3_parser_selection(self, monkeypatch):
    class FakeParams:
      def get_int(self, key):
        return 1 if key == "EnableRadarTracks" else 0

    monkeypatch.setattr(radar_interface_module, "Params", FakeParams)
    monkeypatch.setattr(hyundaicanfd, "Params", FakeParams)
    cp = structs.CarParams()
    cp.carFingerprint = next(car for car, dbc in radar_interface_module.DBC.items() if "hyundai_canfd" in dbc[Bus.pt])
    cp.flags = HyundaiFlags.CANFD.value
    cp.extFlags = HyundaiExtFlags.RADAR_GROUP3.value
    cp.radarUnavailable = False
    cp.safetyConfigs = [structs.CarParams.SafetyConfig()]

    radar_interface = RadarInterface(cp)

    assert radar_interface.radar_group3
    assert radar_interface.radar_start_addr == RADAR_START_ADDR_CANFD3
    assert radar_interface.radar_msg_count == RADAR_MSG_COUNT3
    assert radar_interface.trigger_msg_tracks == 0x41D

    active_dat = bytes.fromhex("e1043b0f02590e692a227e16f80fe00f28fcc753a20a0000")
    empty_dat = bytes.fromhex("c03d3b0000000000ff0700000000000000d0020000000000")
    packets = [(addr, active_dat if addr == 0x406 else empty_dat, 1) for addr in range(0x400, 0x41E)]
    radar_data = radar_interface.update([0, packets])
    point = next(point for point in radar_data.points if point.trackId == 38)

    assert point.measured
    assert point.dRel == pytest.approx(53.1)
    assert point.yRel == pytest.approx(-3.0)
    assert point.vRel == pytest.approx(4.4)



class TestCornerRadar430CandidateFilter:
  @staticmethod
  def slot_word(distance_raw, meta13=0, b2=10, b3=2):
    return distance_raw | (meta13 << 13) | (b2 << 16) | (b3 << 24)

  @classmethod
  def message(cls, slots):
    words = [0x010d1f40] * 7
    for slot, word in slots.items():
      words[slot - 1] = word

    dat = bytearray(32)
    for idx, word in enumerate(words):
      dat[4 + idx * 4:8 + idx * 4] = int(word).to_bytes(4, "little")
    return bytes(dat)

  @staticmethod
  def build_interface(monkeypatch):
    class FakeParams:
      def get_int(self, key):
        return 1 if key == "EnableCornerRadar" else 0

    monkeypatch.setattr(radar_interface_module, "Params", FakeParams)
    monkeypatch.setattr(hyundaicanfd, "Params", FakeParams)
    cp = structs.CarParams()
    cp.carFingerprint = next(car for car, dbc in radar_interface_module.DBC.items() if "hyundai_canfd" in dbc[Bus.pt])
    cp.flags = HyundaiFlags.CANFD.value
    cp.extFlags = HyundaiExtFlags.CORNER_RADAR_OBJECTS_430.value
    cp.radarUnavailable = True
    cp.safetyConfigs = [structs.CarParams.SafetyConfig()]
    return RadarInterface(cp)

  def test_430_requires_neighbor_bin_support(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    single_bin = self.message({6: self.slot_word(1000)})
    packets = [(addr, single_bin if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])

    assert all(not point.measured for point in radar_data.points if point.trackId >= 300)

  def test_430_rejects_background_meta(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    background_bins = self.message({
      6: self.slot_word(1000, b2=3, b3=1),
      7: self.slot_word(1004, b2=3, b3=1),
    })
    packets = [(addr, background_bins if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])

    assert all(not point.measured for point in radar_data.points if point.trackId >= 300)

  def test_430_rejects_low_confidence_meta(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    low_confidence_bins = self.message({
      5: self.slot_word(996, b2=3, b3=2),
      6: self.slot_word(1000, b2=3, b3=2),
      7: self.slot_word(1004, b2=3, b3=2),
    })
    packets = [(addr, low_confidence_bins if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])

    assert all(not point.measured for point in radar_data.points if point.trackId >= 300)

  def test_430_requires_more_support_for_weak_meta(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    weak_two_bins = self.message({
      6: self.slot_word(1000, b2=7, b3=2),
      7: self.slot_word(1004, b2=7, b3=2),
    })
    packets = [(addr, weak_two_bins if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])

    assert all(not point.measured for point in radar_data.points if point.trackId >= 300)

  def test_430_promotes_well_supported_weak_meta(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    weak_three_bins = self.message({
      5: self.slot_word(996, b2=7, b3=2),
      6: self.slot_word(1000, b2=7, b3=2),
      7: self.slot_word(1004, b2=7, b3=2),
    })
    packets = [(addr, weak_three_bins if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])
    points = {point.trackId: point for point in radar_data.points}

    assert points[300].measured
    assert points[300].dRel == pytest.approx(50.0)

  def test_430_promotes_supported_neighbor_bins(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    supported_bins = self.message({
      6: self.slot_word(1000),
      7: self.slot_word(1004),
    })
    packets = [(addr, supported_bins if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, packets])
    radar_data = radar_interface.update([0, packets])
    points = {point.trackId: point for point in radar_data.points}

    assert points[300].measured
    assert points[300].dRel == pytest.approx(50.1)
    assert points[300].yRel == pytest.approx(3.2)
    assert sum(1 for point in radar_data.points if point.trackId >= 300) == 1

  def test_430_keeps_track_id_when_nearer_cluster_disappears(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    first_frame = self.message({
      2: self.slot_word(1000),
      3: self.slot_word(1004),
      6: self.slot_word(1400),
      7: self.slot_word(1404),
    })
    second_frame = self.message({
      6: self.slot_word(1410),
      7: self.slot_word(1414),
    })
    first_packets = [(addr, first_frame if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    first_packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]
    second_packets = [(addr, second_frame if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    second_packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, first_packets])
    radar_interface.update([0, first_packets])
    radar_data = radar_interface.update([0, second_packets])
    points = {point.trackId: point for point in radar_data.points}

    assert points[301].measured
    assert points[301].dRel == pytest.approx(70.6)
    assert points[301].vRel == pytest.approx(3.5)
    assert 300 not in points

  def test_430_rejects_vrel_outlier(self, monkeypatch):
    radar_interface = self.build_interface(monkeypatch)
    empty = self.message({})
    first_frame = self.message({
      6: self.slot_word(1000),
      7: self.slot_word(1004),
    })
    outlier_frame = self.message({
      6: self.slot_word(1024),
      7: self.slot_word(1028),
    })
    first_packets = [(addr, first_frame if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    first_packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]
    outlier_packets = [(addr, outlier_frame if addr == 0x436 else empty, 1) for addr in range(0x430, 0x438)]
    outlier_packets += [(addr, empty, 1) for addr in range(0x440, 0x448)]

    radar_interface.update([0, first_packets])
    radar_interface.update([0, first_packets])
    radar_data = radar_interface.update([0, outlier_packets])

    assert all(not point.measured for point in radar_data.points if point.trackId >= 300)
