import SwiftUI

enum GridTheme {
    static let background = Color(hex: 0x070A10)
    static let surface = Color(hex: 0x10141C)
    static let surfaceRaised = Color(hex: 0x151B25)
    static let textPrimary = Color(hex: 0xF2EFE9)
    static let textSecondary = Color(hex: 0x9AA6B4)
    // Keeps supporting copy subdued while clearing 4.5:1 against raised surfaces.
    static let textTertiary = Color(hex: 0x7A8593)
    static let hairline = Color(hex: 0x7C91AA).opacity(0.12)
    static let liveCyan = Color(hex: 0x7FE3E0)
    static let warning = Color(hex: 0xE05A4D)
    static let staleAmber = Color(hex: 0xE0A24D)
    static let forecastViolet = Color(hex: 0xA98BD6)

    static let horizontalPadding: CGFloat = 22
    static let cornerRadius: CGFloat = 16

    static func fuel(_ fuel: FuelKind) -> Color {
        switch fuel {
        case .wind: Color(hex: 0x7FE3E0)
        case .solar: Color(hex: 0xF2C14E)
        case .nuclear: Color(hex: 0x5B9DFF)
        case .gas: Color(hex: 0xD98A4B)
        case .biomass: Color(hex: 0x7FAE72)
        case .hydro: Color(hex: 0x4FB3B3)
        case .imports: Color(hex: 0xDFE6EE)
        case .storage: Color(hex: 0xA98BD6)
        case .other: Color(hex: 0x8893A1)
        }
    }
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }
}

extension View {
    func gridPageBackground() -> some View {
        background(GridTheme.background.ignoresSafeArea())
            .foregroundStyle(GridTheme.textPrimary)
    }

    func gridMonospacedValue() -> some View {
        fontDesign(.monospaced)
            .monospacedDigit()
    }
}

struct Hairline: View {
    var body: some View {
        Rectangle()
            .fill(GridTheme.hairline)
            .frame(height: 1)
            .accessibilityHidden(true)
    }
}
