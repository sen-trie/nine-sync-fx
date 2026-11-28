#include "settings.fxh"
float4 Shade() : COLOR0 { return float4(Amount, 0.3, 0.6, 1); }
technique Adjust < string Script = "Pass=Screen;"; > {
    pass Screen < string Script = "Draw=Buffer;"; > {
        PixelShader = compile ps_3_0 Shade();
    }
}
